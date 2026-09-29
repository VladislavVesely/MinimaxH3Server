"""Fixed, idempotent execution stages; only OS package operations use sudo."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import codecs
import selectors
import time
import math

from h3server.installer.diagnostics import safe_log, bounded, retry_reason

from h3server.installer.planner import PACKAGE, VERSION, installed_version, package_changes
from h3server.probe import _os_release


class CommandFailure(RuntimeError):
    pass


PENDING_DPKG_STATES = ("unpacked", "half-configured", "triggers-awaited", "triggers-pending")


class ProcessIO:
    """Drain actual child output with a finite deadline; inherit installer ownership.

    Labels are source-owned, never argv (Python snippets and credentials must not
    appear in errors). Diagnostic callbacks are optional for legacy IO callers.
    """
    lock_fd = None

    def __init__(self, command_timeout=3600):
        if not isinstance(command_timeout, (int, float)) or not math.isfinite(command_timeout) or not 0 < command_timeout <= 86400:
            raise ValueError('command deadline must be finite and in (0, 86400] seconds')
        self.command_timeout = command_timeout

    def run(self, argv, *, env, log, capture=False):
        label = Path(argv[0]).name
        notify = lambda name, *args: getattr(log, name, lambda *a: None)(*args)
        notify('started', label)
        collected, size, tail = [], 0, ''
        pending, dropping = '', False
        decoder = codecs.getincrementaldecoder('utf-8')('replace')
        # Only output is sanitized, never the capture consumed by fixed probes.
        secrets = [v for k, v in env.items() if len(v) >= 4 and re.search(r'(?i)password|token|secret|api_key', k)]
        def emit(text):
            nonlocal tail
            for secret in secrets:
                text = text.replace(secret, '[redacted]')
            text = safe_log(text)
            tail = bounded(tail + text, 4096)
            log.write(text)
            log.flush()
        code = None
        try:
            try:
                process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                           stderr=subprocess.STDOUT, env=env, shell=False,
                                           pass_fds=(() if self.lock_fd is None else (self.lock_fd,)))
            except OSError as error:
                if isinstance(error, FileNotFoundError):
                    raise CommandFailure(f'executable {label} not found') from None
                raise OSError(error.errno, f'executable {label} could not be launched') from None
            deadline = time.monotonic() + getattr(self, 'command_timeout', 3600)
            with process, selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                try:
                    while True:
                        notify('flush')
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise CommandFailure('command deadline exceeded')
                        if not selector.select(min(remaining, .5)):
                            continue
                        chunk = os.read(process.stdout.fileno(), 4096)
                        if chunk:
                            notify('activity')
                        text = decoder.decode(chunk, final=not chunk)
                        for part in text.splitlines(keepends=True):
                            if not dropping:
                                pending += part
                                if len(pending) > 65536:
                                    emit('[oversized output line omitted]\n')
                                    pending, dropping = '', True
                            if part.endswith(('\n', '\r')):
                                if not dropping:
                                    emit(pending.replace('\r', '\n'))
                                pending, dropping = '', False
                        if capture:
                            size += len(chunk)
                            if size > 8 * 1024 * 1024:
                                raise CommandFailure('command output exceeds capture limit')
                            collected.append(text)
                        if not chunk:
                            break
                    if pending:
                        emit(pending)
                    code = process.wait(timeout=max(.01, deadline - time.monotonic()))
                except BaseException as error:
                    process.kill()
                    process.wait()
                    if isinstance(error, subprocess.TimeoutExpired):
                        raise CommandFailure('command deadline exceeded') from None
                    raise
            if code:
                raise CommandFailure(f'command {label} exited {code}: {tail.strip() or "no output"}')
        except BaseException as error:
            notify('ended', code, str(error) if isinstance(error, (CommandFailure, OSError)) else 'command interrupted')
            raise
        notify('ended', code)
        return ''.join(collected)


def environment(paths):
    # Ignore user pip/Python configuration; preserve HOME and ordinary OS locale
    # information without allowing PYTHONPATH/PIP_TARGET to escape the app root.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PIP_", "PYTHON", "GIT_"))}
    env.update(paths.cache_environment())
    env.update(LC_ALL="C", PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK="1",
               PIP_NO_INPUT="1", PYTHONUNBUFFERED="1", PYTHONNOUSERSITE="1", GIT_CONFIG_NOSYSTEM="1",
               GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    return env


def atomic_text(path, text):
    temporary = path.with_name(path.name + ".new")
    with temporary.open("w", encoding="utf-8") as out:
        out.write(text)
        out.flush()
        os.fsync(out.fileno())
    temporary.replace(path)


def artifact_path(paths, plan):
    artifact = plan["artifact"]
    path = paths.releases / artifact["release_id"] / artifact["filename"]
    if not path.resolve().is_relative_to(paths.releases):
        raise ValueError("release artifact escapes application root")
    digest = hashlib.sha256()
    with path.open("rb") as file:
        while chunk := file.read(1024 * 1024):
            digest.update(chunk)
    if digest.hexdigest() != artifact["sha256"]:
        raise ValueError("release artifact digest mismatch")
    return path


def change_versions(changes):
    result = set()
    for line in changes:
        match = re.match(r"Inst (\S+)(?: \[[^]]+\])? \((\S+) ", line)
        if not match or not re.fullmatch(PACKAGE, match[1]) or not re.fullmatch(VERSION, match[2]):
            raise CommandFailure("unrecognized APT simulation output")
        result.add((match[1], match[2]))
    return result


PREFLIGHT = '''import sys
assert sys.version_info >= (3, 11)
import torch, torchvision, torchao, transformers, accelerate, peft, huggingface_hub, av
from diffusers import ModularPipeline, ComponentsManager, MiniMaxH3Transformer3DModel
from transformers import Qwen3VLForConditionalGeneration
assert int(huggingface_hub.__version__.split('.')[0]) < 2
assert torch.cuda.is_available(), 'CUDA runtime unavailable; preserve driver and replan compatible dependencies'
x = torch.ones((8, 8), device='cuda'); y = x @ x
torch.cuda.synchronize()
assert y.sum().item() == 512
av.CodecContext.create('libx264', 'w')
av.CodecContext.create('aac', 'w')
print('imports, CUDA allocation/kernel and codec availability passed; model NOT loaded or tested')
'''


class Steps:
    def __init__(self, paths, plan, digest, io, log):
        self.paths, self.plan, self.io, self.log = paths, plan, io, log
        self.env = environment(paths)
        self.directory = paths.environments / digest
        if not self.directory.resolve().is_relative_to(paths.environments):
            raise ValueError("environment escapes application root")
        self.python = str(self.directory / "bin/python")

    def run(self, *argv, capture=False):
        return self.io.run(tuple(argv), env=self.env, log=self.log, capture=capture)

    def phase(self, substage, label):
        if hasattr(self.log, 'phase'):
            self.log.phase(getattr(self, 'stage', 'dependencies'), substage, label)

    def acquire(self, *argv):
        # Only git fetch and pip install enter this boundary. No APT, sudo,
        # checkout, verification or environment mutations are auto-retried.
        pip_install = argv[:4] == (self.python, '-m', 'pip', 'install')
        git_fetch = (len(argv) > 6 and argv[:2] == ('git', '-C')
                     and argv[3:6] == ('-c', 'core.hooksPath=/dev/null', 'fetch'))
        if not (pip_install or git_fetch):
            raise ValueError('only fixed dependency acquisition commands may be retried')
        fresh = False
        for attempt in range(3):
            try:
                return self.run(*argv)
            except CommandFailure as error:
                reason = retry_reason(str(error))
                if reason is None or attempt == 2 or (reason == 'fresh-index' and fresh):
                    raise
                if reason == 'fresh-index':
                    if not pip_install:
                        raise
                    fresh = True
                    # Bypass HTTP/index cache once without deleting wheel caches,
                    # changing pins/indexes, or allowing source builds.
                    argv = (*argv, '--no-cache-dir')
                delay = 2 ** attempt
                if hasattr(self.log, 'retry'):
                    self.log.retry(attempt + 1, reason, delay)
                time.sleep(delay)

    def system_packages(self):
        platform = self.plan["platform"]
        observed = _os_release(self.run("cat", "/etc/os-release", capture=True))
        arch = self.run("uname", "-m", capture=True).strip()
        if observed != {"ID": platform["distribution"], "VERSION_ID": platform["version"]} or arch != platform["architecture"]:
            raise CommandFailure("target platform differs from approved inspection; rescan required")
        if not self.plan["privileged_required"]:
            return
        self.run("sudo", "-n", "true")
        if self.plan["refresh_metadata"]:
            self.run("sudo", "-n", "apt-get", "-o", "APT::Update::Error-Mode=any", "update")
            return
        audit = self.run("dpkg", "--audit", capture=True)
        if audit.strip():
            pending = set(re.findall(r"^ ([a-z0-9][a-z0-9+.:_-]*)\s", audit, re.M))
            allowed = change_versions(self.plan["package_changes"])
            if not pending or not pending.issubset({name for name, _ in allowed}):
                raise CommandFailure("unrelated/incomplete dpkg state requires administrator repair and a new plan")
            # Check EVERY pending package before running any configuration scripts.
            # An approved name alone must not authorize a newly unpacked version.
            for package in sorted(pending):
                text = self.run("dpkg-query", "-W", "-f=${Status}\\t${Version}\\n", package, capture=True)
                match = re.fullmatch(r"install ok (?:" + "|".join(PENDING_DPKG_STATES) + r")\t(" + VERSION + r")\n?", text)
                if not match or (package, match[1]) not in allowed:
                    raise CommandFailure("pending dpkg version/state differs from approved plan; rescan required")
            self.run("sudo", "-n", "env", "DEBIAN_FRONTEND=noninteractive", "dpkg", "--configure", *sorted(pending))
        pins = self.plan["packages"]
        complete = True
        for pin in pins:
            package, version = pin.split("=", 1)
            try:
                text = self.run("dpkg-query", "-W", "-f=${Status}\\t${Version}\\n", package, capture=True)
            except CommandFailure:
                complete = False
                continue
            complete &= installed_version(text) == version
        text = self.run("apt-get", "-s", "--no-remove", "install", *pins, capture=True)
        proposed = change_versions(package_changes(text))
        allowed = change_versions(self.plan["package_changes"])
        if not proposed.issubset(allowed):
            raise CommandFailure("APT changes differ from approved plan; rescan and obtain fresh approval")
        if not complete and not proposed:
            raise CommandFailure("APT did not confirm approved package operations")
        if proposed:
            if self.plan["driver_action"] == "install":
                try:
                    self.run("nvidia-smi")
                except CommandFailure:
                    pass  # the approved plan is specifically for a nonworking driver
                else:
                    raise CommandFailure("driver is now functioning; preserve it and obtain a fresh plan")
            exact_pins = sorted(set(pins) | {f"{name}={version}" for name, version in proposed})
            self.run("sudo", "-n", "env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "-y", "--no-remove", "install", *exact_pins)
            for pin in exact_pins:
                package, version = pin.split("=", 1)
                text = self.run("dpkg-query", "-W", "-f=${Status}\\t${Version}\\n", package, capture=True)
                if installed_version(text) != version:
                    raise CommandFailure("installed package does not match approved version")
        self.run("sudo", "-n", "apt-get", "check")

    def environment(self):
        # Repeating venv creation repairs an interrupted creation without --clear.
        self.run("python3", "-m", "venv", str(self.directory))
        self.run(self.python, "-c", "import sys; assert sys.version_info >= (3, 11); assert sys.prefix != sys.base_prefix")
        atomic_text(self.paths.config / f"installation-{self.directory.name}.json", json.dumps({
            "profile": self.plan["profile"], "backend_revision": self.plan["backend_revision"],
            "model_revision": self.plan["model_revision"], "environment": str(self.directory),
            "artifact": self.plan["artifact"],
        }, sort_keys=True) + "\n")

    def pip_install(self, *arguments):
        # Rich terminal bars suppress intermediate updates on pipes. Newer pip
        # offers newline-delimited measured bytes; older supported pip still
        # emits verbose package/source activity without a made-up percentage.
        self.phase('python-packages', 'Inspect pip progress output capability')
        help_text = self.run(self.python, '-m', 'pip', 'help', 'install', capture=True)
        progress = 'raw' if re.search(r'\braw\b', help_text) else 'off'
        self.phase('python-packages', 'Install Python packages: ' + ', '.join(self.plan['requirements']))
        self.acquire(self.python, '-m', 'pip', 'install', '--progress-bar', progress,
                     '--verbose', '--timeout', '30', '--retries', '0', *arguments)

    def dependencies(self):
        if self.plan.get('runtime') is not None:
            return self.pruned_dependencies()
        wheel = artifact_path(self.paths, self.plan)
        self.pip_install("--index-url", "https://pypi.org/simple",
                 "--only-binary=:all:", "--report", str(self.directory / "resolution.json"), str(wheel), *self.plan["requirements"])
        self.phase("runtime-checks", "Verify installed Python packages (pip check)")
        self.run(self.python, "-m", "pip", "check")
        frozen = self.run(self.python, "-m", "pip", "freeze", "--all", capture=True)
        if not frozen.strip():
            raise CommandFailure("dependency resolution produced no installed versions")
        atomic_text(self.directory / "resolved.txt", frozen)

    def pruned_dependencies(self):
        from h3server.backends.comfy_runtime import verify_sources, runtime_record
        import shutil
        contract = self.plan['runtime']
        if shutil.disk_usage(self.paths.root).free < contract['source_bytes'] + contract['output_reserve_bytes']:
            raise CommandFailure('insufficient source/output reserve; dependency disk use is additional')
        # A prior successful resolution is reused on retry; candidate source and
        # direct constraints remain approval-bound. All IO stays under AppPaths.
        for source in contract['sources']:
            dest = self.directory / source['directory']
            if dest.is_symlink() or not dest.resolve().is_relative_to(self.directory):
                raise CommandFailure('runtime source escapes environment')
            self.phase('runtime-source', 'Fetch ' + source['repository'] + '@' + source['revision'])
            self.run('git', 'init', str(dest))
            self.acquire('git', '-C', str(dest), '-c', 'core.hooksPath=/dev/null',
                     'fetch', '--progress', '--depth=1', '--no-tags', 'https://github.com/' + source['repository'] + '.git', source['revision'])
            self.run('git', '-C', str(dest), '-c', 'core.hooksPath=/dev/null',
                     'checkout', '--detach', source['revision'])
        self.phase('runtime-checks', 'Verify pinned runtime source files')
        verify_sources(self.directory, self.plan['profile'])
        args = []
        for source in contract['sources']:
            args.extend(('-r', str(self.directory / source['directory'] / 'requirements.txt')))
        resolved = self.directory / 'resolved.txt'
        if resolved.exists():
            args.extend(('-c', str(resolved)))
        wheel = artifact_path(self.paths, self.plan)
        self.pip_install('--index-url', 'https://pypi.org/simple',
                 '--only-binary=:all:', '--report', str(self.directory / 'resolution.json'),
                 str(wheel), *self.plan['requirements'], *args)
        self.phase('runtime-checks', 'Verify installed Python packages (pip check)')
        self.run(self.python, '-m', 'pip', 'check')
        frozen = self.run(self.python, '-m', 'pip', 'freeze', '--all', capture=True)
        if not frozen.strip():
            raise CommandFailure('dependency resolution produced no installed versions')
        atomic_text(resolved, frozen)
        atomic_text(self.directory / 'runtime.json', json.dumps(runtime_record(self.plan['profile'], frozen), sort_keys=True))

    def verify(self):
        self.phase("runtime-checks", "Verify installed Python packages (pip check)")
        self.run(self.python, "-m", "pip", "check")
        self.phase('runtime-checks', 'Verify runtime imports, CUDA and codecs; no model weights')
        if self.plan.get('runtime') is not None:
            self.run(self.python, '-m', 'h3server.backends.pruned', '--diagnose', '--environment-only',
                     '--root', str(self.paths.root), '--profile', self.plan['profile'])
        else:
            self.run(self.python, "-c", PREFLIGHT)
        atomic_text(self.directory / "readiness.json", json.dumps({
            "environment_ready": True, "model_ready": False, "generation_tested": False,
        }, sort_keys=True) + "\n")
