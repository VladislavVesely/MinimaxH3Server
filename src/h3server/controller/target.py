"""Fixed Linux deployment operations, executed from a verified staged wheel.

Durable event/operation records outlive SSH. Updates support compatible SQLite
schemas only (Store rejects unknown migrations). Activation preserves model trees,
backs up DBs, restores previous release/environment/unit on failure. An interrupted
activation stays fail-closed until explicit repair; no lease can admit new work.
"""
import argparse
from contextlib import contextmanager, closing
from dataclasses import asdict
from http.client import HTTPConnection
import json
import hashlib
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
import uuid

from h3server.backends import load_manifest
from h3server.backends.base import atomic_json, read_json
from h3server.domain import Event, StageStatus
from h3server.downloads import DownloadCancelled, record_consent, download_model
from h3server.installer.planner import Approval, validate_approval
from h3server.installer.recovery import _private_write, render_units, Recovery
from h3server.installer.runner import Installer
from h3server.installer.steps import artifact_path
from h3server.paths import AppPaths
from h3server.store import Store, SetupState, canonical_plan_digest


class TargetIO:
    def __init__(self, paths):
        self.paths = paths

    def service(self, command, *, allow_absent=False):
        uid = os.getuid()
        env = {**os.environ, **self.paths.cache_environment(), 'XDG_RUNTIME_DIR': f'/run/user/{uid}',
               'DBUS_SESSION_BUS_ADDRESS': f'unix:path=/run/user/{uid}/bus'}
        result = subprocess.run(['systemctl', '--user', *command], stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env, timeout=150)
        if result.returncode:
            if allow_absent and command in (['stop', 'h3server.service'], ['disable', 'h3server.service']):
                # Disabling our externally linked unit removes its registration.
                # Only an authoritative, successful inactive/not-found query may
                # turn a failed cleanup into a no-op; never parse error prose or
                # suppress bus/auth/loaded-unit failures as "already stopped".
                state = subprocess.run(
                    ['systemctl', '--user', 'show', '--property=LoadState',
                     '--property=ActiveState', 'h3server.service'],
                    stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    text=True, env=env, timeout=30)
                lines = state.stdout.splitlines()
                if state.returncode == 0 and len(lines) == 2 and set(lines) == {'LoadState=not-found', 'ActiveState=inactive'}:
                    return
            raise RuntimeError('user service operation failed')

    def api(self, method, path, data=None):
        from h3server.backends.base import regular_file
        with regular_file(self.paths, 'config/remote-token') as stream:
            token = stream.read(258).decode('ascii').strip()
        if not re.fullmatch('[A-Za-z0-9_-]{32,256}', token):
            raise ValueError('invalid daemon token')
        http = HTTPConnection('127.0.0.1', 8765, timeout=10)
        try:
            http.request(method, '/api/v1/' + path, body=None if data is None else json.dumps(data),
                         headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
            response = http.getresponse()
            body = response.read(1024 * 1024 + 1)
            if response.status >= 300 or len(body) > 1024 * 1024:
                raise RuntimeError('remote API operation failed')
            return json.loads(body)
        finally:
            http.close()

    def preflight(self, python):
        # Opening existing DB validates exact schema; only compatible/additive JSON
        # versions are supported. Never attempt an irreversible migration.
        code = 'from h3server.store import Store; import sys; Store(sys.argv[1]); import h3server.remote.app'
        result = subprocess.run([str(python), '-c', code, str(self.paths.state / 'remote.sqlite')],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                env={**os.environ, 'PYTHONPATH': '', 'PYTHONNOUSERSITE': '1'}, timeout=60)
        if result.returncode:
            raise RuntimeError('candidate API/schema preflight failed')

    def health(self):
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            try:
                if self.api('GET', 'health')['service'] == 'ok':
                    return
            except (OSError, RuntimeError, ValueError, KeyError):
                pass
            time.sleep(.5)
        raise RuntimeError('daemon health deadline exceeded')


def active_release(paths):
    try:
        return read_json(paths, 'state/active-release.json')
    except FileNotFoundError:
        return None


def _switch(paths, release):
    pointer = paths.root / ('.current-' + uuid.uuid4().hex)
    os.symlink('releases/' + release, pointer)
    os.replace(pointer, paths.current)
    fd = os.open(paths.root, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _backup(source, destination):
    if source.exists():
        with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(destination)) as dst:
            src.backup(dst)
        destination.chmod(0o600)


class Activation:
    def __init__(self, paths, *, io=None, wait=time.sleep, clock=time.monotonic):
        self.paths = paths
        self.io = io if io is not None else TargetIO(paths)
        self.wait, self.clock = wait, clock

    def run(self, plan, operation, *, policy, cancel_confirmed=False, drain_timeout=300):
        if policy not in {'reject', 'drain', 'cancel'} or (policy == 'cancel' and cancel_confirmed is not True):
            raise ValueError('explicit busy-job policy/cancellation confirmation required')
        if type(drain_timeout) is not int or not 1 <= drain_timeout <= 3600:
            raise ValueError('bounded drain timeout required')
        paths = self.paths
        try:
            prior = read_json(paths, 'state/activation.json')
        except FileNotFoundError:
            prior = None
        if prior and prior['phase'] not in {'complete', 'aborted', 'rolled_back'}:
            raise ValueError('repair interrupted activation before retrying')
        wheel = artifact_path(paths, plan)
        if plan.get('deployment_config') is not None:
            if plan['deployment_config'] != {'port': 8765, 'root': str(paths.root)} or json.loads((wheel.parent / 'daemon.json').read_text()) != plan['deployment_config']:
                raise ValueError('staged daemon configuration differs from approved plan')
        digest = canonical_plan_digest(plan)
        environment = paths.environments / digest
        readiness = json.loads((environment / 'readiness.json').read_text())
        if readiness.get('environment_ready') is not True or not (environment / 'resolved.txt').is_file():
            raise ValueError('verified environment and resolved dependency record required')
        if plan.get('runtime') is not None:
            from h3server.backends.comfy_runtime import runtime_record
            resolved = (environment / 'resolved.txt').read_text()
            if read_json(paths, f'environments/{digest}/runtime.json') != runtime_record(plan['profile'], resolved):
                raise ValueError('candidate runtime differs from approved profile')
        self.io.preflight(environment / 'bin/python')
        previous = active_release(paths)
        unit = render_units(paths, plan)['h3server.service']
        journal = {'operation': operation, 'previous': previous, 'candidate': {'release': plan['artifact']['release_id'],
                   'environment': digest, 'unit': unit}, 'phase': 'prepared'}
        atomic_json(paths, 'state/activation.json', journal)
        gated = stopped = False
        try:
            # A service installed by recovery may already be running after boot.
            try:
                self.io.api('GET', 'health')
                running = True
            except OSError:
                if previous:
                    raise RuntimeError('previous daemon unavailable; repair it before update') from None
                running = False
            if running:
                journal['phase'] = 'draining'
                atomic_json(paths, 'state/activation.json', journal)
                self.io.api('POST', 'maintenance/begin', {'operation': operation, 'policy': policy, 'cancel_confirmed': cancel_confirmed})
                gated = True
                deadline = self.clock() + drain_timeout
                while not self.io.api('GET', 'maintenance')['idle']:
                    if self.clock() >= deadline:
                        raise RuntimeError('drain timed out; no release switched')
                    self.wait(.25)
            journal['phase'] = 'stopping'
            atomic_json(paths, 'state/activation.json', journal)
            self.io.service(['stop', 'h3server.service'], allow_absent=previous is None)
            stopped = True
            if not gated:
                atomic_json(paths, 'state/maintenance.json', {'operation': operation, 'active': True})
                gated = True
            backup = paths.state / ('remote-' + operation + '.backup.sqlite')
            _backup(paths.state / 'remote.sqlite', backup)
            journal.update(phase='switching', backup=backup.name)
            atomic_json(paths, 'state/activation.json', journal)
            _switch(paths, plan['artifact']['release_id'])
            _private_write(paths.config / 'systemd/h3server.service', unit)
            # Fixed runtime configuration is release-owned and never includes auth.
            runtime_document = {
                'root': str(paths.root), 'port': 8765, 'environment': str(environment),
                'profile': plan['profile'], 'backend_revision': plan['backend_revision'],
                'model_revision': plan['model_revision'], 'resolved_dependencies': str(environment / 'resolved.txt'),
                'resolved_sha256': hashlib.sha256((environment / 'resolved.txt').read_bytes()).hexdigest(),
                'dependency_status': 'target-resolved; not a portable transitive hash lock'}
            if plan.get('runtime') is not None:
                runtime_document.update(runtime=plan['runtime'], artifact_set_digest=plan['artifact_set_digest'])
            runtime_path = f"releases/{plan['artifact']['release_id']}/runtime-{digest}.json"
            try:
                if read_json(paths, runtime_path) != runtime_document:
                    raise ValueError('immutable runtime configuration collision')
            except FileNotFoundError:
                atomic_json(paths, runtime_path, runtime_document)
            self.io.service(['daemon-reload'])
            # First-install rollback disables autostart. Only a new approved
            # activation restores the authoritative unit's registration.
            self.io.service(['enable', str(paths.config / 'systemd/h3server.service')])
            self.io.service(['restart', 'h3server.service'])
            self.io.health()
            atomic_json(paths, 'state/active-release.json', journal['candidate'])
            # Commit the selected environment BEFORE reopening submissions. A
            # crash after end must never restore a backup over newly accepted jobs.
            journal['phase'] = 'committed'
            atomic_json(paths, 'state/activation.json', journal)
            if gated:
                self.io.api('POST', 'maintenance/end', {'operation': operation})
            journal['phase'] = 'complete'
            atomic_json(paths, 'state/activation.json', journal)
        except Exception:
            if stopped or journal['phase'] == 'stopping':
                self.rollback(journal)
            else:
                if journal['phase'] == 'draining':
                    self._release_gate(operation)
                journal['phase'] = 'aborted'
                atomic_json(paths, 'state/activation.json', journal)
            raise
        return {'daemon_ready': True, 'installation_ready': True, 'model_ready': False, 'generation_tested': False}

    def _release_gate(self, operation):
        # A lost begin response is ambiguous. Check durable ownership rather
        # than assuming failure or clearing another deployment's gate.
        state = self.io.api('GET', 'maintenance')
        if state['active'] and state['operation'] == operation:
            self.io.api('POST', 'maintenance/end', {'operation': operation})
        elif state['active']:
            raise ValueError('maintenance belongs to another deployment')

    def rollback(self, journal):
        paths, previous = self.paths, journal['previous']
        if journal['phase'] in {'committed', 'rollback_committed'}:
            self.io.health()
            self._release_gate(journal['operation'])
            journal['phase'] = 'complete' if journal['phase'] == 'committed' else 'rolled_back'
            atomic_json(paths, 'state/activation.json', journal)
            return
        if journal['phase'] in {'prepared', 'draining'}:
            if journal['phase'] == 'draining':
                self._release_gate(journal['operation'])
            journal['phase'] = 'aborted'
            atomic_json(paths, 'state/activation.json', journal)
            return
        self.io.service(['stop', 'h3server.service'], allow_absent=previous is None)
        backup_name = journal.get('backup')
        if backup_name:
            if not re.fullmatch(r'remote-[a-f0-9]{32}\.backup\.sqlite', backup_name):
                raise ValueError('invalid backup record')
            backup = paths.state / backup_name
            if backup.exists():
                # No daemon or worker owns the DB; remove stale WAL before restore.
                for suffix in ('-wal', '-shm'):
                    (paths.state / ('remote.sqlite' + suffix)).unlink(missing_ok=True)
                _backup(backup, paths.state / 'remote.sqlite')
        if previous:
            _switch(paths, previous['release'])
            _private_write(paths.config / 'systemd/h3server.service', previous['unit'])
            self.io.service(['daemon-reload'])
            self.io.service(['restart', 'h3server.service'])
            self.io.health()
            atomic_json(paths, 'state/active-release.json', previous)
            journal['phase'] = 'rollback_committed'
            atomic_json(paths, 'state/activation.json', journal)
            self._release_gate(journal['operation'])
        else:
            # Recovery registered this environment directly, not via current.
            # Removing the pointer alone cannot prevent autostart at next boot.
            # Keep the journal/gate unresolved if disabling registration fails.
            self.io.service(['disable', 'h3server.service'], allow_absent=True)
            paths.current.unlink(missing_ok=True)
            (paths.state / 'active-release.json').unlink(missing_ok=True)
            # No previous service to authenticate to; we hold the operation lock
            # with daemon stopped, so clear this exact owner's durable gate.
            try:
                gate = read_json(paths, 'state/maintenance.json')
                if gate['operation'] == journal['operation']:
                    atomic_json(paths, 'state/maintenance.json', {**gate, 'active': False})
            except FileNotFoundError:
                pass
        journal['phase'] = 'rolled_back'
        atomic_json(paths, 'state/activation.json', journal)


@contextmanager
def operation_lock(paths):
    import fcntl
    fd = os.open(paths.state / 'deployment.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield fd
    finally:
        os.close(fd)


def _detach(keep_fd):
    """Fork/reap launcher, retain operation flock across disconnect/parent death."""
    pid = os.fork()
    if pid:
        _, code = os.waitpid(pid, 0)
        if code:
            raise RuntimeError('detached operation failed')
        return False
    os.setsid()
    if os.fork():
        os._exit(0)
    fd = os.open(os.devnull, os.O_RDWR)
    for target in (0, 1, 2):
        os.dup2(fd, target)
    import resource
    maximum = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
    maximum = 65536 if maximum == resource.RLIM_INFINITY else maximum
    os.closerange(3, keep_fd)
    os.closerange(keep_fd + 1, maximum)
    return True


def snapshot(paths, store, after):
    from h3server.events import ReplayGap
    try:
        result = Recovery(paths, store).snapshot(after=after)
    except ReplayGap as gap:
        result = Recovery(paths, store).snapshot(after=gap.minimum_cursor)
        result['reset'] = True
    result['active_release'] = active_release(paths)
    for name in ('operation', 'activation'):
        try:
            result[name] = read_json(paths, f'state/{name}.json')
        except FileNotFoundError:
            result[name] = None
    if result['operation'] and result['operation']['state'] == 'running':
        try:
            with operation_lock(paths):
                result['operation']['state'] = 'interrupted'
        except BlockingIOError:
            pass
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--digest', required=True)
    parser.add_argument('action', choices=('snapshot', 'activate', 'download', 'cancel-download', 'repair', 'install-update'))
    args = parser.parse_args()
    os.umask(0o077)
    if os.geteuid() == 0 or not re.fullmatch('[a-f0-9]{64}', args.digest):
        raise ValueError('unprivileged digest-bound operation required')
    paths = AppPaths(args.root)
    data = json.loads(sys.stdin.buffer.read(32769))
    plan = read_json(paths, f'bootstrap/{args.digest}/plan.json')
    if canonical_plan_digest(plan) != args.digest:
        raise ValueError('stale plan')
    store = Store(paths.state / 'setup.sqlite3')
    if args.action == 'snapshot':
        print(json.dumps(snapshot(paths, store, data['after'])))
        return
    if args.action == 'cancel-download':
        record = read_json(paths, 'state/operation.json')
        if record['action'] != 'download' or record['state'] != 'running':
            raise ValueError('no running download')
        atomic_json(paths, 'state/download-cancel.json', {'operation': record['operation']})
        print(json.dumps({'state': 'cancellation_requested'}))
        return
    approval = Approval(**read_json(paths, f'bootstrap/{args.digest}/approval.json'))
    validate_approval(plan, approval)
    if data.get('plan_digest') != args.digest:
        raise ValueError('approval required')
    if args.action == 'download':
        record_consent(paths, license_sha256=data.get('license_sha256'),
            license_acknowledged=data.get('license_acknowledged'), eligibility_confirmed=data.get('eligibility_confirmed'),
            download_approved=data.get('download_approved'), profile_name=plan['profile'],
            artifact_set_digest=data.get('artifact_set_digest'))
    if args.action == 'activate' and (data.get('activation_approved') is not True or data.get('policy') not in {'reject', 'drain', 'cancel'}
                                    or (data['policy'] == 'cancel' and data.get('cancel_confirmed') is not True)):
        raise ValueError('activation approval required')
    if args.action == 'repair' and data.get('rollback_approved') is not True:
        raise ValueError('rollback approval required')
    with operation_lock(paths) as fd:
        setups = [s for s in store.list_setups(limit=1000) if s.plan_digest == args.digest]
        if args.action == 'install-update' and not setups:
            setups = [Installer(paths, store).create(plan)]
        if len(setups) != 1:
            raise ValueError('setup checkpoint required')
        setup = setups[0]
        if args.action in {'activate', 'download'} and setup.state != SetupState.READY:
            raise ValueError('verified environment required')
        operation = uuid.uuid4().hex
        record = {'operation': operation, 'action': args.action, 'state': 'running', 'plan_digest': args.digest}
        atomic_json(paths, 'state/operation.json', record)
        if not _detach(fd):
            print(json.dumps({'state': 'accepted', 'operation': operation}))
            return
        try:
            if args.action == 'activate':
                with Installer(paths, store).lock():
                    Activation(paths).run(plan, operation, policy=data['policy'], cancel_confirmed=data.get('cancel_confirmed', False))
            elif args.action == 'repair':
                journal = read_json(paths, 'state/activation.json')
                if journal['phase'] not in {'prepared', 'draining', 'stopping', 'switching', 'committed', 'rollback_committed'}:
                    raise ValueError('no interrupted activation to repair')
                with Installer(paths, store).lock():
                    Activation(paths).rollback(journal)
            elif args.action == 'install-update':
                # OS/driver mutation during an update needs a separate maintenance
                # window. Never replace the working user unit during staging.
                if plan['privileged_required'] or plan['reboot_required']:
                    raise ValueError('update requires administrator prerequisite maintenance')
                Installer(paths, store).run(setup.setup_id, approval)
            else:
                def cancelled():
                    try:
                        return read_json(paths, 'state/download-cancel.json')['operation'] == operation
                    except FileNotFoundError:
                        return False
                download_model(paths, emit=lambda event: store.append_event('setup', setup.setup_id, event), cancelled=cancelled,
                               profile_name=plan['profile'])
            record['state'] = 'completed'
            store.append_event('setup', setup.setup_id, Event(args.action, StageStatus.COMPLETED, 'Operation completed; model loading and generation require separate validation.'))
        except DownloadCancelled:
            record['state'] = 'cancelled'
            store.append_event('setup', setup.setup_id, Event(args.action, StageStatus.CANCELLED, 'Model download cancelled; verified files and partial bytes remain resumable.'))
        except Exception:
            record['state'] = 'failed'
            store.append_event('setup', setup.setup_id, Event(args.action, StageStatus.FAILED, 'Operation failed or cancelled; inspect checkpoint and retry/repair. No readiness assumed.'))
        finally:
            atomic_json(paths, 'state/operation.json', record)
        os._exit(0)


if __name__ == '__main__':
    main()
