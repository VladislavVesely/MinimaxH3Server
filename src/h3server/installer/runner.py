"""Resumable Linux target installer and fixed POSIX bootstrap contract.

Integration: inspect_prerequisites -> build_plan -> present full changes/license.
For prerequisite-only rounds use SSHConnection.install_prerequisites (stdin sudo)
or stage_bootstrap + launch_detached with preauthorized scoped sudo -n. For an
environment plan, stage the approved inputs/wheel, approve recovery.service_plan,
then Recovery.prepare finishes privilege before user-systemd continuation.
On WAITING_FOR_REPLAN the controller must automatically inspect again, replace
this inactive plan with Store.update_setup_plan, and request fresh approval in
its existing wizard. It must not reuse the old digest. On WAITING_FOR_REBOOT,
checkpoint is durable; a separate reboot owner checks Approval.reboot and the
same digest before any reboot. Resume after reconnect reruns the driver check.
READY here means only a verified candidate environment, not model/service ready.

The bootstrap executes without Python until approved distro packages supply it.
It accepts only our staged pure-Python wheel, never an HTTP script or command.
"""
import argparse
from contextlib import contextmanager
from dataclasses import asdict
import json
import os
from pathlib import Path
import re
import shlex
import subprocess

from h3server.domain import StageStatus
from h3server.installer.diagnostics import Diagnostics
from h3server.installer.planner import Approval, validate_approval, validate_plan
from h3server.installer.steps import (
    CommandFailure, PENDING_DPKG_STATES, ProcessIO, Steps, artifact_path, atomic_text, change_versions,
)
from h3server.paths import AppPaths
from h3server.store import SetupState, Store, canonical_plan_digest


class InstallerBusy(RuntimeError):
    pass


class Installer:
    def __init__(self, paths: AppPaths, store: Store, *, io=None, inherited_lock_fd=None, boot_id=None):
        self.paths, self.store = paths, store
        self.io = io if io is not None else ProcessIO()
        self.inherited_lock_fd = inherited_lock_fd
        self.boot_id = boot_id

    @contextmanager
    def lock(self):
        # Linux target only; controller import remains safe on Windows.
        import fcntl
        path = self.paths.state / "installer.lock"
        if self.inherited_lock_fd is None:
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        else:
            fd = os.dup(self.inherited_lock_fd)
            if (os.fstat(fd).st_dev, os.fstat(fd).st_ino) != (path.stat().st_dev, path.stat().st_ino):
                os.close(fd)
                raise ValueError("inherited installer lock does not match target")
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as e:
                raise InstallerBusy("another installer or its child is running") from e
            self.io.lock_fd = fd
            yield
        finally:
            self.io.lock_fd = None
            os.close(fd)  # don't explicitly unlock; surviving children retain ownership

    def create(self, plan):
        validate_plan(plan)
        checkpoint = self.store.create_setup(plan)
        return self.store.transition_setup(checkpoint.setup_id, SetupState.AWAITING_APPROVAL)

    def run(self, setup_id, approval: Approval, *, stop_after_packages=False, unprivileged=False):
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            raise PermissionError("application/model Python must run as the unprivileged SSH user")
        with self.lock():
            checkpoint = self.store.get_setup(setup_id)
            plan = checkpoint.plan
            validate_approval(plan, approval)
            artifact_path(self.paths, plan)
            if unprivileged and checkpoint.stages[0].status != StageStatus.COMPLETED:
                raise PermissionError("privileged prerequisites must finish before detached continuation")
            if checkpoint.state in {SetupState.READY, SetupState.WAITING_FOR_REPLAN}:
                return checkpoint
            if checkpoint.state == SetupState.PLANNED:
                checkpoint = self.store.transition_setup(setup_id, SetupState.AWAITING_APPROVAL)
            if checkpoint.state == SetupState.AWAITING_APPROVAL:
                self.store.approve_setup(setup_id, approval.plan_digest)
            if checkpoint.state == SetupState.WAITING_FOR_REBOOT:
                from h3server.installer.recovery import boot_changed
                if not boot_changed(self.paths, approval.plan_digest, self.boot_id):
                    return checkpoint
                self.store.transition_setup(setup_id, SetupState.RECONNECTING)
            self.store.transition_setup(setup_id, SetupState.RUNNING)
            with Diagnostics(self.paths, setup_id, approval.plan_digest) as log:
                steps = Steps(self.paths, plan, approval.plan_digest, self.io, log)
                current = None
                try:
                    for stage in self.store.get_setup(setup_id).stages:
                        current = stage.name
                        if stage.status == StageStatus.COMPLETED:
                            if stage.name == "system-packages" and stop_after_packages:
                                return self.store.get_setup(setup_id)
                            continue
                        steps.stage = stage.name
                        log.phase(stage.name, stage.name, stage.name.replace("-", " "))
                        self.store.transition_stage(setup_id, stage.name, StageStatus.RUNNING)
                        if stage.name == "replan":
                            self.store.transition_stage(setup_id, stage.name, StageStatus.COMPLETED)
                            return self.store.transition_setup(setup_id, SetupState.WAITING_FOR_REPLAN)
                        if stage.name == "environment" and plan["reboot_required"]:
                            try:
                                steps.run("nvidia-smi")
                            except CommandFailure:
                                from h3server.installer.recovery import record_waiting_boot
                                record_waiting_boot(self.paths, approval.plan_digest, self.boot_id)
                                return self.store.transition_setup(setup_id, SetupState.WAITING_FOR_REBOOT)
                        getattr(steps, stage.name.replace("-", "_"))()
                        self.store.transition_stage(setup_id, stage.name, StageStatus.COMPLETED)
                        if stage.name == "system-packages" and stop_after_packages:
                            return self.store.get_setup(setup_id)
                    return self.store.transition_setup(setup_id, SetupState.READY)
                except BaseException as error:
                    log.failed(error)
                    if current is not None:
                        self.store.transition_stage(setup_id, current, StageStatus.FAILED)
                    self.store.transition_setup(setup_id, SetupState.FAILED)
                    raise


def stage_bootstrap(paths: AppPaths, plan: dict, approval: Approval) -> Path:
    """Write inspectable script + immutable inputs on target BEFORE detaching.

    An SSH/SFTP caller may transfer these same bytes to matching target paths;
    no shell interpolation of user text is required. This function is target-side
    filesystem IO. Password/sudo authentication is intentionally not stored.
    """
    validate_approval(plan, approval)
    wheel = artifact_path(paths, plan)
    for directory in paths.directories():
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = paths.bootstrap / approval.plan_digest
    target.mkdir(mode=0o700, exist_ok=True)
    atomic_text(target / "plan.json", json.dumps(plan, sort_keys=True))
    atomic_text(target / "approval.json", json.dumps(asdict(approval), sort_keys=True))
    # APT versions in these files are already validated, never shell fragments.
    allowed = sorted(" ".join(pair) for pair in change_versions(plan["package_changes"]))
    atomic_text(target / "allowed-packages", "\n".join(allowed) + "\n")
    atomic_text(target / "allowed-names", "\n".join(sorted({name for name, _ in change_versions(plan["package_changes"])})) + "\n")
    q = shlex.quote
    exports = "\n".join("export " + key + "=" + q(value) for key, value in paths.cache_environment().items())
    pins = " ".join(q(pin) for pin in plan["packages"])
    exact_pins = " ".join(q(pin) for pin in sorted(set(plan["packages"]) | {f"{name}={version}" for name, version in change_versions(plan["package_changes"])}))
    package_script = ""
    # The shell must supply Python only when absent. Do not perform any app Python
    # as root or bootstrap from curl/get-pip. apt metadata-only plans can stop
    # before Python exists; marker mandates a new inspection/approval round.
    if plan["refresh_metadata"]:
        package_script = '''sudo -n apt-get -o APT::Update::Error-Mode=any update
printf '%s\\n' waiting_for_replan > "$stage/status"
exit 0
'''
    elif plan["purpose"] == "prerequisites" and pins:
        package_script = f'''sudo -n true
dpkg --audit > "$stage/dpkg-audit"
if test -s "$stage/dpkg-audit"; then
    awk '/^ [a-z0-9]/ {{ print $1 }}' "$stage/dpkg-audit" | sort -u > "$stage/pending"
    test -s "$stage/pending" || exit 1
    comm -23 "$stage/pending" "$stage/allowed-names" > "$stage/unapproved"
    if test -s "$stage/unapproved"; then echo 'Unrelated dpkg state; administrator repair required'; exit 1; fi
    # Validate all current states/versions before any privileged configuration.
    while IFS= read -r package; do
        dpkg-query -W -f='${{Status}}\\t${{Version}}\\n' "$package" > "$stage/pending-status"
        version=$(awk -F '\\t' 'NR == 1 && NF == 2 && $1 ~ /^install ok ({'|'.join(PENDING_DPKG_STATES)})$/ {{ value=$2; valid=1 }} END {{ if (NR != 1 || !valid) exit 1; print value }}' "$stage/pending-status")
        if ! grep -Fx -- "$package $version" "$stage/allowed-packages" > /dev/null; then
            echo 'Pending dpkg version differs from approved plan; rescan required'; exit 1
        fi
    done < "$stage/pending"
    # Tokens are a subset of generated, validated package names (no shell code).
    sudo -n env DEBIAN_FRONTEND=noninteractive dpkg --configure $(cat "$stage/pending")
fi
apt-get -s --no-remove install {pins} > "$stage/apt-simulation"
awk '$1 == "Remv" {{ exit 1 }} $1 == "Inst" {{ for (i=3;i<=NF;i++) if (substr($i,1,1)=="(") {{ print $2,substr($i,2); break }} }}' "$stage/apt-simulation" > "$stage/proposed-unsorted"
sort -u "$stage/proposed-unsorted" > "$stage/proposed"
comm -23 "$stage/proposed" "$stage/allowed-packages" > "$stage/unapproved"
if test -s "$stage/unapproved"; then echo 'APT plan changed; fresh approval required'; exit 1; fi
sudo -n env DEBIAN_FRONTEND=noninteractive apt-get -y --no-remove install {exact_pins}
'''
    distro_pattern = q('^ID=[\'\"]?' + re.escape(plan["platform"]["distribution"]) + '[\'\"]?$')
    version_pattern = q('^VERSION_ID=[\'\"]?' + re.escape(plan["platform"]["version"]) + '[\'\"]?$')
    script = f'''#!/bin/sh
# Generated fixed H3 bootstrap contract v1; inspect before approval/execution.
set -eu
umask 077
stage={q(str(target))}
exec >> "$stage/bootstrap.log" 2>&1
if test "$(id -u)" = 0; then echo 'Run as the unprivileged SSH user'; exit 1; fi
exec 9>{q(str(paths.state / 'installer.lock'))}
flock -n 9 || exit 75
trap 'code=$?; if test "$code" -ne 0; then printf "%s\\n" failed > "$stage/status"; fi' 0
export LC_ALL=C
{exports}
printf '%s\\n' running > "$stage/status"
grep -Eq {distro_pattern} /etc/os-release
grep -Eq {version_pattern} /etc/os-release
test "$(uname -m)" = x86_64
printf '%s  %s\\n' {q(plan['artifact']['sha256'])} {q(str(wheel))} | sha256sum -c -
{package_script}python3 -c 'import sys, venv, ensurepip; assert sys.version_info >= (3, 11)'
printf '%s\\n' python_ready > "$stage/status"
# zipimport of the verified pure-Python wheel needs no pip or application deps.
unset PYTHONHOME PYTHONSTARTUP
export PYTHONPATH={q(str(wheel))}
export PYTHONNOUSERSITE=1
exec python3 -m h3server.installer.runner --root {q(str(paths.root))} --plan "$stage/plan.json" --approval "$stage/approval.json" --lock-fd 9
'''
    path = target / "bootstrap.sh"
    atomic_text(path, script)
    path.chmod(0o700)
    return path


def launch_detached(script: Path, *, popen=subprocess.Popen):
    """Target-side launcher; returns PID handle, NOT successful installation.

    Legacy prerequisite path with preauthorized scoped sudo -n. Environment
    installation uses Recovery.prepare instead: registered unprivileged user
    units resume at boot with no saved credentials. This setsid launcher alone
    survives disconnect but does not promise startup after reboot.
    """
    return popen(("/bin/sh", str(script)), stdin=subprocess.DEVNULL,
                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                 start_new_session=True, close_fds=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--approval", required=True)
    parser.add_argument("--lock-fd", type=int)
    args = parser.parse_args()
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        raise PermissionError("installer Python must not run as root")
    paths = AppPaths(args.root)
    plan = json.loads(Path(args.plan).read_text())
    approval = Approval(**json.loads(Path(args.approval).read_text()))
    validate_approval(plan, approval)
    store = Store(paths.state / "setup.sqlite3")
    runner = Installer(paths, store, inherited_lock_fd=args.lock_fd)
    # Lookup/create is under the same lock, so two detached invocations cannot
    # create duplicate active checkpoints for one digest.
    try:
        with runner.lock():
            existing = next((s for s in store.list_setups(limit=1000) if s.plan_digest == canonical_plan_digest(plan)), None)
            setup = existing if existing else runner.create(plan)
            # Reuse the same open-file description for nested run ownership.
            runner.inherited_lock_fd = runner.io.lock_fd
            result = runner.run(setup.setup_id, approval)
    except BaseException:
        atomic_text(Path(args.plan).parent / "status", "failed\n")
        raise
    atomic_text(Path(args.plan).parent / "status", result.state.value + "\n")


if __name__ == "__main__":
    main()
