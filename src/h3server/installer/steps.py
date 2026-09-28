"""Fixed, idempotent execution stages; only OS package operations use sudo."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import unicodedata

from h3server.installer.planner import PACKAGE, VERSION, installed_version, package_changes
from h3server.probe import _os_release


class CommandFailure(RuntimeError):
    pass


PENDING_DPKG_STATES = ("unpacked", "half-configured", "triggers-awaited", "triggers-pending")


def safe_log(text):
    # Never forward terminal controls or URL userinfo into replay/UI logs.
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"(https?://)[^/@\s]+:[^/@\s]+@", r"\1[redacted]@", text)
    return "".join(c for c in text if c in "\n\t" or unicodedata.category(c) not in {"Cc", "Cf"})


class ProcessIO:
    """Drain merged output in bounded chunks; no shell, stdin or synthetic percent.

    The installer lock is inherited by children: killing a supervisor cannot
    release ownership while its apt/pip child still mutates the environment.
    """
    lock_fd = None

    def run(self, argv, *, env, log, capture=False):
        collected = []
        size = 0
        pending, dropping = "", False
        try:
            process = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.STDOUT, env=env, shell=False,
                                       pass_fds=(() if self.lock_fd is None else (self.lock_fd,)))
        except FileNotFoundError as error:
            # Missing optional probes (not permission or other IO failures) share
            # the failed-command boundary used by driver discovery/reboot checks.
            raise CommandFailure(f"executable {Path(argv[0]).name} not found") from error
        with process:
            try:
                while chunk := process.stdout.read1(4096):
                    text = chunk.decode("utf-8", "replace")
                    for part in text.splitlines(keepends=True):
                        if not dropping:
                            pending += part
                            if len(pending) > 65536:
                                log.write("[oversized output line omitted]\n")
                                pending = ""
                                dropping = True
                        if part.endswith(("\n", "\r")):
                            if not dropping:
                                log.write(safe_log(pending))
                            pending, dropping = "", False
                            log.flush()
                    if capture:
                        size += len(chunk)
                        if size > 8 * 1024 * 1024:
                            raise CommandFailure("command output exceeds capture limit")
                        collected.append(text)
                if pending:
                    log.write(safe_log(pending))
                    log.flush()
                code = process.wait()
            except BaseException:
                process.terminate()
                process.wait()
                raise
        if code:
            raise CommandFailure(f"command {Path(argv[0]).name} exited {code}; see installer log")
        return "".join(collected)


def environment(paths):
    # Ignore user pip/Python configuration; preserve HOME and ordinary OS locale
    # information without allowing PYTHONPATH/PIP_TARGET to escape the app root.
    env = {k: v for k, v in os.environ.items() if not k.startswith(("PIP_", "PYTHON"))}
    env.update(paths.cache_environment())
    env.update(LC_ALL="C", PIP_CONFIG_FILE=os.devnull, PIP_DISABLE_PIP_VERSION_CHECK="1",
               PIP_NO_INPUT="1", PYTHONNOUSERSITE="1", GIT_CONFIG_NOSYSTEM="1",
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

    def dependencies(self):
        wheel = artifact_path(self.paths, self.plan)
        self.run(self.python, "-m", "pip", "install", "--index-url", "https://pypi.org/simple",
                 "--only-binary=:all:", "--report", str(self.directory / "resolution.json"), str(wheel), *self.plan["requirements"])
        self.run(self.python, "-m", "pip", "check")
        frozen = self.run(self.python, "-m", "pip", "freeze", "--all", capture=True)
        if not frozen.strip():
            raise CommandFailure("dependency resolution produced no installed versions")
        atomic_text(self.directory / "resolved.txt", frozen)

    def verify(self):
        self.run(self.python, "-m", "pip", "check")
        self.run(self.python, "-c", PREFLIGHT)
        atomic_text(self.directory / "readiness.json", json.dumps({
            "environment_ready": True, "model_ready": False, "generation_tested": False,
        }, sort_keys=True) + "\n")
