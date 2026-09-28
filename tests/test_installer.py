"""Real filesystem/SQLite and injectable process boundaries; no OS mutations."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from h3server.installer.planner import ReleaseArtifact, build_plan
from h3server.installer.runner import Installer, InstallerBusy, launch_detached, stage_bootstrap
from h3server.installer.steps import CommandFailure, ProcessIO
from h3server.paths import AppPaths
from h3server.store import SetupState, Store
from test_install_plan import PROFILE, approval, facts


def prepared(tmp_path, **kw):
    paths = AppPaths(tmp_path / "app")
    for d in paths.directories():
        d.mkdir(parents=True, exist_ok=True)
    release = paths.releases / "v1"
    release.mkdir()
    wheel = release / "h3server-0.1.0-py3-none-any.whl"
    wheel.write_bytes(b"verified fixture wheel, not executable")
    import hashlib
    p = build_plan(facts(**kw), profile=PROFILE,
                   artifact=ReleaseArtifact("v1", wheel.name, hashlib.sha256(wheel.read_bytes()).hexdigest()))
    return paths, p, Store(paths.state / "setup.sqlite3")


class FakeIO:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    def run(self, argv, *, env, log, capture=False):
        self.calls.append((argv, env))
        if self.fail and self.fail in " ".join(argv):
            raise CommandFailure("injected failure")
        if argv == ("cat", "/etc/os-release"):
            return 'ID=ubuntu\nVERSION_ID="24.04"'
        if argv == ("uname", "-m"):
            return "x86_64"
        if "-m" in argv and "venv" in argv:
            python = Path(argv[-1]) / "bin/python"
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("fixture")
        if "freeze" in argv:
            return "torch==2.14.0\nhuggingface-hub==1.50.0\n"
        return ""


def test_ready_means_environment_only_and_repeat_is_noop(tmp_path):
    paths, p, store = prepared(tmp_path)
    io = FakeIO()
    runner = Installer(paths, store, io=io)
    setup = runner.create(p)
    result = runner.run(setup.setup_id, approval(p))
    assert result.state == SetupState.READY
    count = len(io.calls)
    runner.run(setup.setup_id, approval(p))
    assert len(io.calls) == count
    env = paths.environments / result.plan_digest
    assert "torch==2.14.0" in (env / "resolved.txt").read_text()
    receipt = json.loads((env / "readiness.json").read_text())
    assert receipt == {"environment_ready": True, "model_ready": False, "generation_tested": False}
    for argv, environment in io.calls:
        assert argv[0] != "sudo"
        assert environment["HOME"] == os.environ["HOME"]
        assert environment["TMPDIR"] == str(paths.tmp)
        assert environment["PIP_CONFIG_FILE"] == os.devnull


def test_fail_then_resume_only_unfinished_stages(tmp_path):
    paths, p, store = prepared(tmp_path)
    io = FakeIO(fail="pip install")
    runner = Installer(paths, store, io=io)
    setup = runner.create(p)
    with pytest.raises(CommandFailure):
        runner.run(setup.setup_id, approval(p))
    assert store.get_setup(setup.setup_id).state == SetupState.FAILED
    assert not list(paths.environments.glob("*/readiness.json"))
    io.fail = None
    runner.run(setup.setup_id, approval(p))
    assert sum("venv" in argv for argv, _ in io.calls) == 1


def test_artifact_tamper_and_stale_approval_execute_nothing(tmp_path):
    paths, p, store = prepared(tmp_path)
    io = FakeIO()
    runner = Installer(paths, store, io=io)
    setup = runner.create(p)
    next(paths.releases.glob("*/*.whl")).write_bytes(b"changed")
    with pytest.raises(ValueError, match="digest"):
        runner.run(setup.setup_id, approval(p))
    assert not io.calls
    assert store.get_setup(setup.setup_id).state == SetupState.AWAITING_APPROVAL


def test_exclusive_lock(tmp_path):
    paths, p, store = prepared(tmp_path)
    runner = Installer(paths, store, io=FakeIO())
    setup = runner.create(p)
    with runner.lock():
        with pytest.raises(InstallerBusy):
            runner.run(setup.setup_id, approval(p))


def test_discovery_checkpoint_reopens_and_replaces_plan(tmp_path):
    paths, p, store = prepared(tmp_path, python_ready=False)
    runner = Installer(paths, store, io=FakeIO())
    setup = runner.create(p)
    result = runner.run(setup.setup_id, approval(p))
    assert result.state == SetupState.WAITING_FOR_REPLAN
    reopened = Store(store.path)
    assert reopened.get_setup(setup.setup_id) == result
    assert runner.run(setup.setup_id, approval(p)) == result
    next_plan = dict(p, purpose="environment", stages=["system-packages", "environment", "dependencies", "verify"])
    updated = reopened.update_setup_plan(setup.setup_id, next_plan)
    assert updated.approved_digest is None and updated.state == SetupState.PLANNED


def test_logs_are_streamed_sanitized_and_failure_is_not_swallowed(tmp_path):
    log = tmp_path / "process.log"
    io = ProcessIO()
    with log.open("a", encoding="utf-8") as out:
        text = io.run((sys.executable, "-c", "print('ok\\x1b[31m'); print('x' * 100000)"),
                      env=dict(os.environ), log=out, capture=True)
        assert "ok" in text
        with pytest.raises(CommandFailure):
            io.run((sys.executable, "-c", "raise SystemExit(4)"), env=dict(os.environ), log=out)
    assert "\x1b" not in log.read_text()
    assert "[oversized output line omitted]" in log.read_text()


def test_bootstrap_is_posix_inspectable_no_python_required_before_apt(tmp_path):
    paths, p, store = prepared(tmp_path, python_ready=False,
                               packages=("python3=3.12", "python3-venv=3.12"),
                               package_changes=("Inst python3 (3.12 Ubuntu [amd64])", "Inst python3-venv (3.12 Ubuntu [amd64])"))
    script = stage_bootstrap(paths, p, approval(p, privileged=True))
    subprocess.run(("sh", "-n", str(script)), check=True)
    text = script.read_text()
    assert text.index("apt-get") < text.index("python3 -m h3server")
    assert "sha256sum" in text and "flock" in text
    assert "HOME=" not in text.replace("XDG_CACHE_HOME=", "").replace("HF_HOME=", "").replace("TORCH_HOME=", "")
    assert "curl" not in text and "wget" not in text
    called = []
    launch_detached(script, popen=lambda *args, **kwargs: called.append((args, kwargs)))
    assert called[0][1]["start_new_session"] is True


def test_missing_sudo_is_failure_not_success(tmp_path):
    paths, p, store = prepared(tmp_path, packages=("git=1",), package_changes=("Inst git (1 Debian [amd64])",))
    io = FakeIO(fail="sudo")
    runner = Installer(paths, store, io=io)
    setup = runner.create(p)
    with pytest.raises(CommandFailure):
        runner.run(setup.setup_id, approval(p, privileged=True))
    assert store.get_setup(setup.setup_id).state == SetupState.FAILED


class AptIO(FakeIO):
    def __init__(self, *, simulation="Inst git (1 Debian [amd64])\n", audit="", driver=False):
        super().__init__()
        self.simulation, self.audit, self.driver = simulation, audit, driver
        self.installed = False

    def run(self, argv, **kw):
        result = super().run(argv, **kw)
        if argv[0] == "dpkg" and "--audit" in argv:
            return self.audit
        if argv[0] == "dpkg-query":
            return "install ok installed\t1\n" if self.installed else ""
        if argv[:2] == ("apt-get", "-s"):
            return self.simulation
        if "apt-get" in argv and "-y" in argv:
            self.installed = True
        if argv == ("nvidia-smi",) and not self.driver:
            raise CommandFailure("driver unavailable")
        return result


def test_apt_recovery_and_exact_pinned_install(tmp_path):
    paths, p, store = prepared(tmp_path, packages=("git=1",), package_changes=("Inst git (1 Debian [amd64])",))
    io = AptIO(audit="The following packages are unpacked:\n git Git tool\n")
    runner = Installer(paths, store, io=io)
    result = runner.run(runner.create(p).setup_id, approval(p, privileged=True))
    assert result.state == SetupState.READY
    assert any(argv[-3:] == ("dpkg", "--configure", "git") for argv, _ in io.calls)
    assert not any("-a" in argv for argv, _ in io.calls)
    assert any("git=1" in argv and "-y" in argv for argv, _ in io.calls)


@pytest.mark.parametrize("overrides", [
    {"simulation": "Inst git (2 Debian [amd64])"},
    {"simulation": "Remv unrelated [1]"},
    {"audit": "The following packages are unpacked:\n unrelated Tool\n"},
])
def test_apt_unapproved_changes_never_install(tmp_path, overrides):
    paths, p, store = prepared(tmp_path, packages=("git=1",), package_changes=("Inst git (1 Debian [amd64])",))
    io = AptIO(**overrides)
    runner = Installer(paths, store, io=io)
    with pytest.raises((ValueError, CommandFailure)):
        runner.run(runner.create(p).setup_id, approval(p, privileged=True))
    assert not any("-y" in argv or "--configure" in argv for argv, _ in io.calls)


def test_driver_reboot_checkpoint_never_reboots_or_reinstalls(tmp_path):
    paths, p, store = prepared(tmp_path, driver_working=False, driver_package="nvidia-driver-580",
                               packages=("nvidia-driver-580=1",), package_changes=("Inst nvidia-driver-580 (1 Ubuntu [amd64])",))
    io = AptIO(simulation="Inst nvidia-driver-580 (1 Ubuntu [amd64])")
    runner = Installer(paths, store, io=io)
    setup = runner.create(p)
    waiting = runner.run(setup.setup_id, approval(p, privileged=True))
    assert waiting.state == SetupState.WAITING_FOR_REBOOT
    assert not any("reboot" in argv for argv, _ in io.calls)
    again = runner.run(setup.setup_id, approval(p, privileged=True, reboot=True))
    assert again.state == SetupState.WAITING_FOR_REBOOT
    assert sum("-y" in argv for argv, _ in io.calls) == 1
    io.driver = True
    assert runner.run(setup.setup_id, approval(p, privileged=True)).state == SetupState.READY


def test_preflight_failure_never_publishes_readiness(tmp_path):
    paths, p, store = prepared(tmp_path)
    io = FakeIO(fail="torch.cuda")
    runner = Installer(paths, store, io=io)
    with pytest.raises(CommandFailure):
        runner.run(runner.create(p).setup_id, approval(p))
    assert not list(paths.environments.glob("*/readiness.json"))


def test_child_environment_discards_python_pip_escape_hatches(tmp_path, monkeypatch):
    from h3server.installer.steps import environment
    monkeypatch.setenv("PYTHONPATH", "/untrusted")
    monkeypatch.setenv("PIP_TARGET", "/outside")
    monkeypatch.setenv("PIP_INDEX_URL", "https://user:secret@other.example/")
    env = environment(AppPaths(tmp_path))
    assert "PIP_TARGET" not in env and "PYTHONPATH" not in env and "PIP_INDEX_URL" not in env


def test_stream_redacts_credentials_across_read_boundaries(tmp_path):
    code = "import sys,time; sys.stdout.write('https://user:'); sys.stdout.flush(); time.sleep(.02); print('secret@example.org/x')"
    log = tmp_path / "log"
    with log.open("w") as out:
        ProcessIO().run((sys.executable, "-c", code), env=dict(os.environ), log=out)
    assert "secret" not in log.read_text()
    assert "[redacted]" in log.read_text()


def test_root_is_rejected_before_any_process(tmp_path, monkeypatch):
    paths, p, store = prepared(tmp_path)
    io = FakeIO()
    runner = Installer(paths, store, io=io)
    setup = runner.create(p)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    with pytest.raises(PermissionError):
        runner.run(setup.setup_id, approval(p))
    assert not io.calls


def test_replan_state_cannot_hide_unfinished_stages(tmp_path):
    from h3server.store import InvalidTransition
    paths, p, store = prepared(tmp_path, python_ready=False)
    runner = Installer(paths, store, io=FakeIO())
    setup = runner.create(p)
    store.approve_setup(setup.setup_id, setup.plan_digest)
    store.transition_setup(setup.setup_id, SetupState.RUNNING)
    with pytest.raises(InvalidTransition):
        store.transition_setup(setup.setup_id, SetupState.WAITING_FOR_REPLAN)


def test_running_stage_retries_after_process_interruption(tmp_path):
    from h3server.domain import StageStatus
    paths, p, store = prepared(tmp_path)
    io = FakeIO()
    runner = Installer(paths, store, io=io)
    setup = runner.create(p)
    store.approve_setup(setup.setup_id, setup.plan_digest)
    store.transition_setup(setup.setup_id, SetupState.RUNNING)
    store.transition_stage(setup.setup_id, "system-packages", StageStatus.RUNNING)
    resumed = Installer(paths, Store(store.path), io=io).run(setup.setup_id, approval(p))
    assert resumed.state == SetupState.READY


def test_native_platform_changed_since_inspection_blocks_mutation(tmp_path):
    paths, p, store = prepared(tmp_path)
    class WrongPlatform(FakeIO):
        def run(self, argv, **kw):
            if argv == ("uname", "-m"):
                return "aarch64"
            return super().run(argv, **kw)
    io = WrongPlatform()
    runner = Installer(paths, store, io=io)
    with pytest.raises(CommandFailure, match="platform"):
        runner.run(runner.create(p).setup_id, approval(p))
    assert not any("sudo" in argv or "venv" in argv for argv, _ in io.calls)


def test_child_inherits_lock_until_it_exits(tmp_path):
    paths, p, store = prepared(tmp_path)
    runner = Installer(paths, store, io=FakeIO())
    with runner.lock():
        fd = runner.io.lock_fd
        child = subprocess.Popen((sys.executable, "-c", "import sys; sys.stdin.read()"),
                                 stdin=subprocess.PIPE, pass_fds=(fd,))
    try:
        with pytest.raises(InstallerBusy):
            with runner.lock():
                pass
    finally:
        child.communicate(timeout=5)
    with runner.lock():
        pass


def test_transitive_package_versions_are_explicit_at_execution(tmp_path):
    paths, p, store = prepared(tmp_path, packages=("git=1",),
                               package_changes=("Inst git (1 Debian [amd64])", "Inst git-man (1 Debian [amd64])"))
    io = AptIO(simulation="Inst git (1 Debian [amd64])\nInst git-man (1 Debian [amd64])\n")
    runner = Installer(paths, store, io=io)
    runner.run(runner.create(p).setup_id, approval(p, privileged=True))
    install = next(argv for argv, _ in io.calls if "-y" in argv)
    assert "git=1" in install and "git-man=1" in install
