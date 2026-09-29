"""Linux target recovery and controller reconnect contracts (no import-time IO).

Task9: the approved user daemon runs ``<environment>/bin/python -m
h3server.remote --root <root>``. It MUST listen on loopback only. A service being
registered/active never proves model readiness or generation. Task11: present
service_plan separately from the installation plan, then call prepare with both
digests/approvals. prepare completes all privileged work while credentials are
in memory; the enabled user bootstrap calls resume without credentials at boot.
Use request_reboot only after a WAITING_FOR_REBOOT event and separate consent.
Snapshot is durable Store state + exclusive-cursor replay (ReplayGap propagates).
Reopening a controller requires reauthentication; reconnect never approves keys.

Prerequisite-only rounds (including Python absent) use
SSHConnection.install_prerequisites(root, plan, approval, sudo_password=...) for
password-only sudo, or runner.stage_bootstrap with existing scoped sudo -n. The
SSH path reuses Steps.system_packages locally with fixed, flock-protected OS
commands remotely in one transaction-wide guardian; bounded credential frames
reach sudo through an isolated anonymous pipe, never its command's stdin. Both
paths must replan and cannot register Python continuation until Python exists.
No root Python, sudoers edits, downloaded shell, automatic Secure Boot enrollment,
or automatic reboot on failure. Native systemd/VM acceptance is separate.
"""
import argparse
from dataclasses import asdict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time
from uuid import UUID

from h3server.domain import Event, StageStatus
from h3server.installer.planner import Approval, validate_approval, validate_plan, validate_reboot_approval
from h3server.installer.steps import CommandFailure, ProcessIO, artifact_path, change_versions, environment
from h3server.paths import AppPaths
from h3server.store import ApprovalRequired, SetupState, Store, canonical_plan_digest


# Templates are embedded so a staged pure-Python wheel works before pip install.
# Repository templates mirror these contracts; no cwd-dependent resource lookup.
BOOTSTRAP_UNIT = '''[Unit]
Description=H3 approved unprivileged bootstrap continuation
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
Environment=@ENVIRONMENT@
ExecStart=/usr/bin/python3 -m h3server.installer.recovery --root @ROOT@ --digest @DIGEST@ resume
TimeoutStartSec=infinity
KillMode=control-group
UMask=0077
NoNewPrivileges=yes
Restart=no

[Install]
WantedBy=default.target
'''
DAEMON_UNIT = '''[Unit]
Description=H3 loopback user daemon
After=network-online.target h3-bootstrap.service
Wants=network-online.target
ConditionPathExists=@READINESS@

[Service]
Type=simple
ExecStart=@PYTHON@ -m h3server.remote --root @ROOT@
Environment=@ENVIRONMENT@
WorkingDirectory=@WORKINGDIRECTORY@
Restart=on-failure
RestartSec=5
TimeoutStopSec=120
KillMode=control-group
UMask=0077
NoNewPrivileges=yes

[Install]
WantedBy=default.target
'''


def _private_write(path, document):
    """Atomic 0600 replacement + directory fsync before any reboot IO."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.new')
    fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        os.fchmod(stream.fileno(), 0o600)
        stream.write(document)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def read_boot_id():
    value = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    return str(UUID(value))


def _boot_path(paths, digest):
    if not re.fullmatch('[a-f0-9]{64}', digest):
        raise ValueError('invalid plan digest')
    directory = paths.bootstrap / digest
    if not directory.resolve().is_relative_to(paths.bootstrap):
        raise ValueError('bootstrap escapes application root')
    return directory / 'boot.json'


def boot_changed(paths, digest, boot_id=None):
    record = json.loads(_boot_path(paths, digest).read_text())
    if record['plan_digest'] != digest:
        raise ApprovalRequired('boot checkpoint digest mismatch')
    return record['boot_id'] != str(UUID((boot_id or read_boot_id)()))


def record_waiting_boot(paths, digest, boot_id=None):
    path = _boot_path(paths, digest)
    if path.exists():
        if boot_changed(paths, digest, boot_id):
            raise CommandFailure('driver unavailable after reboot; manual signed-driver/Secure Boot review required')
        return
    _private_write(path, json.dumps({'plan_digest': digest, 'boot_id': str(UUID((boot_id or read_boot_id)())),
                                    'reboot_requested': False}))


def _quote(value, *, exec_value=False):
    value = str(value)
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ValueError('control character in systemd value')
    value = value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%')
    if exec_value:
        value = value.replace('$', '$$')
    return '"' + value + '"'


def _scalar_path(value):
    # These directives consume the entire rvalue, not ExecStart's quoted tokens.
    # Only specifiers expand. Quotes/backslashes inside the path remain literal.
    value = str(value)
    if (not value.startswith('/') or value != value.rstrip() or value.endswith('\\')
            or any(ord(c) < 32 or ord(c) == 127 for c in value)):
        raise ValueError('path cannot be represented as a systemd scalar')
    return value.replace('%', '%%')


def render_units(paths, plan):
    digest = canonical_plan_digest(plan)
    wheel = artifact_path(paths, plan)
    base_env = paths.cache_environment()
    base_env.update(PYTHONNOUSERSITE='1', PYTHONHOME='', PYTHONSTARTUP='')
    bootstrap_env = {**base_env, 'PYTHONPATH': str(wheel)}
    daemon_env = {**base_env, 'PYTHONPATH': ''}
    def env(values):
        return ' '.join(_quote(f'{k}={v}') for k, v in values.items())
    directory = paths.environments / digest
    if not directory.resolve().is_relative_to(paths.environments):
        raise ValueError('service environment escapes application root')
    python = directory / 'bin/python'
    def substitute(template, values):
        return re.sub(r'@([A-Z]+)@', lambda match: values[match[1]], template)
    bootstrap = substitute(BOOTSTRAP_UNIT, {'ROOT': _quote(paths.root, exec_value=True),
                                          'DIGEST': digest, 'ENVIRONMENT': env(bootstrap_env)})
    daemon = substitute(DAEMON_UNIT, {'ROOT': _quote(paths.root, exec_value=True),
                                     'PYTHON': _quote(python, exec_value=True),
                                     'READINESS': _scalar_path(python.parent.parent / 'readiness.json'),
                                     'WORKINGDIRECTORY': _scalar_path(paths.root),
                                     'ENVIRONMENT': env(daemon_env)})
    return {'h3-bootstrap.service': bootstrap, 'h3server.service': daemon}


def _unit_path(paths, name):
    path = paths.config / 'systemd' / name
    if not path.resolve().is_relative_to(paths.config):
        raise ValueError('unit path escapes application config')
    return path


def service_plan(paths, plan, *, uid):
    if type(uid) is not int or uid <= 0:
        raise ValueError('service owner must be an unprivileged uid')
    return {'schema': 1, 'stages': ['privileged-prerequisites', 'linger', 'register-user-units'],
            'root': str(paths.root), 'uid': uid, 'plan_digest': canonical_plan_digest(plan),
            'linger': True, 'daemon_contract': 'python -m h3server.remote --root <root>; loopback only',
            'units': {name: hashlib.sha256(text.encode()).hexdigest() for name, text in render_units(paths, plan).items()},
            'changes': ['Enable lingering for this user (starts at boot, survives logout).',
                        'Register user bootstrap and daemon; authoritative files remain under config/systemd.',
                        'No sudo grant, root service, model download or generation. Reboot requires separate consent.']}


class PrivilegedIO(ProcessIO):
    """In-memory sudo credential, only for exact approved OS command tuples.

    All privileged output is discarded (even malicious password echoes); logs
    contain only a fixed outcome. No PTY, shell, askpass or persisted sudo token.
    Wrong passwords/requiretty/policy denial fail; never fall back to a prompt.
    """
    def __init__(self, plan, *, uid, password=None, run=subprocess.run):
        if password is not None and (not isinstance(password, str) or not password or len(password) > 4096 or any(c in password for c in '\r\n\x00')):
            raise ValueError('invalid sudo credential')
        self._password, self._run = password, run
        versions = change_versions(plan['package_changes'])
        pins = tuple(sorted(set(plan['packages']) | {f'{name}={version}' for name, version in versions}))
        self._allowed = {('true',), ('loginctl', 'enable-linger', str(uid)), ('systemctl', 'reboot')}
        if plan['privileged_required']:
            self._allowed.add(('apt-get', 'check'))
            if plan['refresh_metadata']:
                self._allowed.add(('apt-get', '-o', 'APT::Update::Error-Mode=any', 'update'))
            elif pins:
                self._allowed.add(('env', 'DEBIAN_FRONTEND=noninteractive', 'apt-get', '-y', '--no-remove', 'install', *pins))
        self._packages = {name for name, _ in versions}
        self._pins = set(pins)
        self._required_pins = set(plan['packages'])

    def run(self, argv, *, env, log, capture=False):
        if argv[0] != 'sudo':
            return super().run(argv, env=env, log=log, capture=capture)
        command = tuple(argv[2:])
        dpkg = command[:4] == ('env', 'DEBIAN_FRONTEND=noninteractive', 'dpkg', '--configure') and bool(command[4:]) and set(command[4:]).issubset(self._packages)
        # Steps revalidates every pending version before this fixed dpkg action.
        install = (command[:6] == ('env', 'DEBIAN_FRONTEND=noninteractive', 'apt-get', '-y', '--no-remove', 'install')
                   and bool(command[6:]) and self._required_pins.issubset(command[6:]) and set(command[6:]).issubset(self._pins))
        if argv[:2] != ('sudo', '-n') or (command not in self._allowed and not dpkg and not install) or capture:
            raise PermissionError('sudo operation is outside approved scope')
        actual = ('sudo', '-n', '--', *command)
        if self._password is not None:
            # subprocess.run closes this finite input pipe after the one secret.
            # NOPASSWD must not pass unread credentials to package hooks. Only
            # this constant wrapper and already-allowlisted OS argv run as root.
            actual = ('sudo', '-S', '-k', '-p', '', '--', '/bin/sh', '-c',
                      'cat >/dev/null || exit 76; exec "$@" </dev/null',
                      'h3-approved-privilege', *command)
        try:
            result = self._run(actual, input=None if self._password is None else self._password + '\n',
                               text=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               env=env, timeout=3600, check=False,
                               pass_fds=(() if self.lock_fd is None else (self.lock_fd,)))
            success = result.returncode == 0
        except (OSError, subprocess.SubprocessError):
            success = False
        log.write('Approved privileged operation succeeded.\n' if success else 'Privileged operation failed; reauthenticate or seek administrator policy review.\n')
        log.flush()
        if not success:
            raise CommandFailure('privileged operation failed; authentication/policy or command failure (output suppressed)')
        return ''


class _UnprivilegedIO:
    def __init__(self, delegate):
        self.delegate = delegate
    @property
    def lock_fd(self):
        return self.delegate.lock_fd
    @lock_fd.setter
    def lock_fd(self, value):
        self.delegate.lock_fd = value
    def run(self, argv, **kw):
        if argv[0] == 'sudo':
            raise PermissionError('no privileged operations in detached continuation')
        return self.delegate.run(argv, **kw)


class PrerequisiteProtocol:
    """Finite, plan-bound POSIX transaction; no remote Python or eval/RPC.

    One flock open-file description spans staging, checks, mutation and status.
    A non-execing unprivileged supervisor retains it while sudo closes its own
    inherited descriptors. EOF is read only after children are reaped. A lost
    controller therefore cannot release exclusion while an OS operation runs.

    Credential frames are bounded and copied by dd into an anonymous sudo pipe,
    never read into shell variables. A fixed root wrapper drains unused input
    (NOPASSWD), verifies non-secret dd transfer counts, then redirects the actual
    approved OS operation's stdin to /dev/null. Frame statistics contain counts,
    not credentials, and are removed after both pipeline children exit.
    """
    def __init__(self, root, plan, approval):
        import shlex
        validate_approval(plan, approval)
        if plan['purpose'] != 'prerequisites':
            raise ValueError('expected a prerequisite plan')
        self.root, self.plan, self.approval = root, plan, approval
        versions = change_versions(plan['package_changes'])
        self.pins = set(plan['packages']) | {f'{name}={version}' for name, version in versions}
        self.names = {pin.split('=')[0] for pin in self.pins}
        self.static = {
            'os': ('cat', '/etc/os-release'), 'arch': ('uname', '-m'),
            'audit': ('dpkg', '--audit'),
            'simulate': ('apt-get', '-s', '--no-remove', 'install', *plan['packages']),
        }
        for index, name in enumerate(sorted(self.names)):
            self.static[f'query{index}'] = ('dpkg-query', '-W', '-f=${Status}\\t${Version}\\n', name)
        if plan['privileged_required']:
            self.static.update({'authorize': ('sudo', '-n', 'true'),
                                'check': ('sudo', '-n', 'apt-get', 'check')})
            if plan['refresh_metadata']:
                self.static['update'] = ('sudo', '-n', 'apt-get', '-o', 'APT::Update::Error-Mode=any', 'update')
        self.dynamic = {}
        if plan['privileged_required'] and self.pins and not plan['refresh_metadata']:
            self.dynamic = {
                'install': (('sudo', '-n', 'env', 'DEBIAN_FRONTEND=noninteractive', 'apt-get', '-y', '--no-remove', 'install'), self.pins),
                'configure': (('sudo', '-n', 'env', 'DEBIAN_FRONTEND=noninteractive', 'dpkg', '--configure'), self.names),
            }
        cases = []
        for operation, argv in self.static.items():
            privileged = argv[:2] == ('sudo', '-n')
            command = argv[2:] if privileged else argv
            cases.append(f'{operation}) test "$argc" -eq 0 || exit 76; privileged={int(privileged)}; set -- {shlex.join(command)};;')
        for operation, (prefix, allowed) in self.dynamic.items():
            pattern = '|'.join(shlex.quote(a) for a in sorted(allowed))
            cases.append(f'''{operation})
    test "$argc" -gt 0 && test "$argc" -le {len(allowed)} || exit 76
    privileged=1; set -- {shlex.join(prefix[2:])}
    while test "$argc" -gt 0; do
        IFS= read -r argument || exit 76
        case "$argument" in {pattern}) ;; *) exit 76;; esac
        set -- "$@" "$argument"; argc=$((argc - 1))
    done;;''')
        self.cases = '\n'.join(cases)

    def request(self, argv, password=None):
        for operation, command in self.static.items():
            if tuple(argv) == command:
                return operation, (), password if command[0] == 'sudo' else None
        for operation, (prefix, allowed) in self.dynamic.items():
            arguments = tuple(argv[len(prefix):])
            if tuple(argv[:len(prefix)]) == prefix and arguments and len(arguments) <= len(allowed) and set(arguments).issubset(allowed):
                if operation == 'install' and not set(self.plan['packages']).issubset(arguments):
                    break
                return operation, arguments, password
        raise PermissionError('command is outside this prerequisite transaction')

    def command(self, ready_token):
        import shlex
        q = shlex.quote
        stage = self.root + '/bootstrap/' + canonical_plan_digest(self.plan)
        # All script/OS command bytes derive from validated approved inputs.
        # The only controller-supplied runtime fields are a finite ID, allowed
        # package tokens, a bounded frame length and an unpredictable reply nonce.
        wrapper = '''cat >/dev/null || exit 76
if test "$1" != -; then
    grep -Fxq '1+0 records in' "$1" && grep -Fxq '1+0 records out' "$1" && grep -Fxq 0 "$2" || exit 76
fi
shift 2
exec "$@" </dev/null'''
        script = f'''umask 077
export LC_ALL=C
stage={q(stage)}
mkdir -p -- "$stage" || exit 76
finished=no
trap 'if test "$finished" != yes; then printf "%s\\n" failed > "$stage/status"; fi' 0
printf '%s\\n' {q(json.dumps(self.plan, sort_keys=True))} > "$stage/plan.json.new" && mv -- "$stage/plan.json.new" "$stage/plan.json" || exit 76
printf '%s\\n' {q(json.dumps(asdict(self.approval), sort_keys=True))} > "$stage/approval.json.new" && mv -- "$stage/approval.json.new" "$stage/approval.json" || exit 76
printf '%s\\n' running > "$stage/status" || exit 76
printf '%s\\n' {q('H3READY ' + ready_token)}
while IFS=' ' read -r operation argc size nonce extra; do
    test -z "$extra" || exit 76
    case "$argc:$size" in *[!0-9:]*|:*|*:) exit 76;; esac
    test "${{#argc}}" -le 6 && test "${{#size}}" -le 5 && test "$size" -le 16385 || exit 76
    case "$nonce" in ''|*[!a-f0-9]*) exit 76;; esac
    test "${{#nonce}}" -eq 32 || exit 76
    privileged=0
    case "$operation" in
{self.cases}
finish) test "$argc:$size" = 0:0 || exit 76
    printf '%s\\n' waiting_for_replan > "$stage/status" || exit 76
    finished=yes; printf '\\nH3DONE %s 0\\n' "$nonce"; exit 0;;
*) exit 76;;
    esac
    if test "$privileged" -eq 1; then
        if test "$size" -gt 0; then
            # Neither shell read nor the approved command can consume this pipe.
            # The subshell deliberately waits, retaining flock even if sudo
            # closes its descriptors or the guardian itself is interrupted.
            ( dd bs="$size" count=1 iflag=fullblock 2>"$stage/frame-stat"; result=$?; printf '%s\\n' "$result" > "$stage/frame-code" ) |
                ( sudo -S -k -p '' -- /bin/sh -c {q(wrapper)} h3-prerequisite "$stage/frame-stat" "$stage/frame-code" "$@" >/dev/null 2>&1; result=$?; exit "$result" )
            code=$?
            grep -Fxq '1+0 records in' "$stage/frame-stat" && grep -Fxq '1+0 records out' "$stage/frame-stat" && grep -Fxq 0 "$stage/frame-code" || exit 76
            rm -f -- "$stage/frame-stat" "$stage/frame-code" || exit 76
        else
            ( sudo -n -- "$@" </dev/null >/dev/null 2>&1; result=$?; exit "$result" )
            code=$?
        fi
    else
        test "$size" -eq 0 || exit 76
        "$@" </dev/null; code=$?
    fi
    printf '\\nH3DONE %s %s\\n' "$nonce" "$code"
done
exit 76
'''
        lock = self.root + '/state/installer.lock'
        # Only lock-parent directory creation precedes exclusion, not checkpoint
        # contents or package probes. flock's fd stays inherited by supervisors.
        return ('umask 077; mkdir -p -- ' + q(self.root + '/state') +
                ' && exec setsid --wait flock -n -E 75 ' + q(lock) +
                ' /bin/sh -c ' + q(script))


class Recovery:
    def __init__(self, paths, store, *, io=None, uid=None, boot_id=None, password=None):
        self.paths, self.store = paths, store
        self.io, self.uid = io, os.getuid() if uid is None else uid
        self.boot_id, self._password = boot_id, password

    def _runner(self, plan, *, unprivileged=False):
        from h3server.installer.runner import Installer
        io = self.io if self.io is not None else PrivilegedIO(plan, uid=self.uid, password=self._password)
        if unprivileged:
            io = _UnprivilegedIO(io)
        return Installer(self.paths, self.store, io=io, boot_id=self.boot_id)

    def _command(self, runner, *argv):
        env = environment(self.paths)
        # Noninteractive SSH need not export the logind user-manager bus address.
        # Derive it from the approved owner, never from a caller-supplied bus URL.
        env.update(XDG_RUNTIME_DIR=f'/run/user/{self.uid}',
                   DBUS_SESSION_BUS_ADDRESS=f'unix:path=/run/user/{self.uid}/bus')
        with (self.paths.logs / 'recovery.log').open('a', encoding='utf-8') as log:
            return runner.io.run(tuple(argv), env=env, log=log, capture=argv[0] == 'loginctl')

    def _receipt_path(self, digest):
        return _boot_path(self.paths, digest).parent / 'recovery.json'

    def _receipt(self, setup, digest):
        if digest != setup.plan_digest:
            raise ApprovalRequired('stale recovery digest')
        record = json.loads(self._receipt_path(digest).read_text())
        approval = Approval(**record['approval'])
        validate_approval(setup.plan, approval)
        if approval.privileged is not True:
            raise ApprovalRequired('service registration requires privileged consent')
        expected = service_plan(self.paths, setup.plan, uid=self.uid)
        if record['setup_id'] != setup.setup_id or record['service_plan'] != expected or record['service_digest'] != canonical_plan_digest(expected):
            raise ApprovalRequired('service approval mismatch; reapprove registration')
        for name, text in render_units(self.paths, setup.plan).items():
            if _unit_path(self.paths, name).read_text() != text:
                raise ApprovalRequired('registered unit changed; reapprove registration')
        return approval

    def prepare(self, setup_id, approval, *, service_digest):
        setup = self.store.get_setup(setup_id)
        validate_approval(setup.plan, approval)
        document = service_plan(self.paths, setup.plan, uid=self.uid)
        if approval.privileged is not True or service_digest != canonical_plan_digest(document):
            raise ApprovalRequired('separate digest-bound service/lingering and privileged consent required')
        if setup.plan['purpose'] != 'environment':
            raise ApprovalRequired('finish prerequisite discovery and approve a new environment plan first')
        runner = self._runner(setup.plan)
        with runner.lock():
            runner.inherited_lock_fd = runner.io.lock_fd
            result = runner.run(setup_id, approval, stop_after_packages=True)
            runner.io.lock_fd = runner.inherited_lock_fd
            try:
                # Packages are durable; only fixed logind registration needs root.
                self._command(runner, 'sudo', '-n', 'loginctl', 'enable-linger', str(self.uid))
                if self._command(runner, 'loginctl', 'show-user', str(self.uid), '-p', 'Linger', '--value').strip() != 'yes':
                    raise CommandFailure('lingering was not confirmed; no boot continuation promised')
                for name, text in render_units(self.paths, setup.plan).items():
                    _private_write(_unit_path(self.paths, name), text)
                self._command(runner, 'systemctl', '--user', 'daemon-reload')
                for name in render_units(self.paths, setup.plan):
                    self._command(runner, 'systemctl', '--user', 'enable', str(_unit_path(self.paths, name)))
                _private_write(self._receipt_path(setup.plan_digest), json.dumps({
                    'setup_id': setup_id, 'approval': asdict(approval), 'service_plan': document, 'service_digest': service_digest}, sort_keys=True))
                self.store.append_event('setup', setup_id, Event('service-registration', StageStatus.COMPLETED,
                                        'User units registered with lingering; daemon health/model/generation not tested.'))
            except Exception:
                if self.store.get_setup(setup_id).state == SetupState.RUNNING:
                    self.store.transition_setup(setup_id, SetupState.FAILED)
                self.store.append_event('setup', setup_id, Event('service-registration', StageStatus.FAILED,
                                        'User-unit registration failed; authentication/policy or service-manager review required.'))
                raise
        # Unit takes the same installer flock; never start while holding it.
        self._command(runner, 'systemctl', '--user', 'start', '--no-block', 'h3-bootstrap.service')
        return result

    def resume(self, setup_id, digest):
        setup = self.store.get_setup(setup_id)
        approval = self._receipt(setup, digest)
        runner = self._runner(setup.plan, unprivileged=True)
        return runner.run(setup_id, approval, unprivileged=True)

    def request_reboot(self, setup_id, approval):
        setup = self.store.get_setup(setup_id)
        validate_reboot_approval(setup.plan, approval)
        self._receipt(setup, approval.plan_digest)
        runner = self._runner(setup.plan)
        with runner.lock():
            setup = self.store.get_setup(setup_id)
            if setup.state != SetupState.WAITING_FOR_REBOOT or setup.stages[0].status != StageStatus.COMPLETED:
                raise ApprovalRequired('reboot requires durable waiting state and completed privileged prerequisites')
            path = _boot_path(self.paths, setup.plan_digest)
            record = json.loads(path.read_text())
            if boot_changed(self.paths, setup.plan_digest, self.boot_id):
                raise ApprovalRequired('boot already changed; resume instead of rebooting again')
            if record['reboot_requested']:
                return setup  # at-most-once: ambiguous/disconnected requests require manual review
            record.update(reboot_requested=True, reboot_approval=asdict(approval))
            _private_write(path, json.dumps(record, sort_keys=True))
            self._command(runner, 'sudo', '-n', 'systemctl', 'reboot')
        return setup

    def snapshot(self, *, after=0, limit=100):
        page = self.store.read_events(after=after, limit=limit)
        return {'setups': [asdict(s) for s in self.store.list_setups(limit=1000)],
                'events': [asdict(e) for e in page.events], 'cursor': page.next_cursor, 'has_more': page.has_more}


def reconnect(factory, target, password, *, timeout=120, cancel=None, clock=time.monotonic, wait=None):
    """Existing KnownHosts pinning is mandatory; factory creates SSHConnection.

    Caller owns an in-memory password and returned authenticated connection.
    Cancellation checked between bounded (<=5s) IO attempts; no key approval,
    password logging/persistence, indefinite retry or automatic installer launch.
    """
    from h3server.controller.ssh import ControllerError
    if password is None:
        raise ControllerError('authentication_required', 401)
    if not math.isfinite(timeout) or not 0 < timeout <= 3600:
        raise ValueError('reconnect timeout must be in (0, 3600]')
    cancel = cancel if cancel is not None else threading.Event()
    wait = wait or cancel.wait
    deadline, delay = clock() + timeout, 0.5
    while clock() < deadline:
        if cancel.is_set():
            raise ControllerError('cancelled')
        connection = factory()
        try:
            status = connection.connect(*target, timeout=min(5, deadline - clock()))
            if status['state'] != 'awaiting_authentication':
                raise ControllerError('trust_required')
            if cancel.is_set():
                raise ControllerError('cancelled')
            remaining = deadline - clock()
            if remaining <= 0:
                raise ControllerError('reconnect_timeout', 504)
            connection.authenticate(password, timeout=min(5, remaining))
            if clock() >= deadline:
                raise ControllerError('reconnect_timeout', 504)
            if cancel.is_set():
                raise ControllerError('cancelled')
            return connection
        except ControllerError as error:
            connection.close()
            if error.code != 'connection_failed':
                raise
        if wait(min(delay, max(0, deadline - clock()))):
            raise ControllerError('cancelled')
        delay = min(delay * 2, 8)
    raise ControllerError('reconnect_timeout', 504)


def _detach():
    """Linux double fork after reading credentials; close all SSH descriptors.

    Parent reaps the intermediate child. Grandchild is reaped by the OS, not an
    abandoned Popen handle. Credentials exist only in private process memory.
    """
    pid = os.fork()
    if pid:
        _, status = os.waitpid(pid, 0)
        if status:
            raise CommandFailure('detached launcher failed')
        return False
    try:
        os.setsid()
        if os.fork():
            os._exit(0)
        # No inherited SSH pipe stays open, and no credential echo reaches logs.
        fd = os.open(os.devnull, os.O_RDWR)
        for target in (0, 1, 2):
            os.dup2(fd, target)
        if fd > 2:
            os.close(fd)
        # Launch path has no Store connection open and no owned process children.
        import resource
        maximum = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
        os.closerange(3, maximum if maximum != resource.RLIM_INFINITY else 65536)
        return True
    except BaseException:
        os._exit(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--digest', required=True)
    parser.add_argument('--detach', action='store_true')
    parser.add_argument('--service-digest')
    parser.add_argument('action', choices=('service-plan', 'prepare', 'reboot', 'resume', 'snapshot'))
    parser.add_argument('--after', type=int, default=0)
    args = parser.parse_args()
    if os.geteuid() == 0:
        raise PermissionError('application recovery must not run as root')
    paths = AppPaths(args.root)
    if args.action == 'prepare':
        for directory in paths.directories():
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    stage = _boot_path(paths, args.digest).parent
    if args.action == 'service-plan':
        if args.detach:
            raise ValueError('service plan inspection cannot detach')
        plan = json.loads((stage / 'plan.json').read_text())
        validate_plan(plan)
        if canonical_plan_digest(plan) != args.digest:
            raise ApprovalRequired('stale staged plan')
        print(json.dumps(service_plan(paths, plan, uid=os.getuid())))
        return
    password = None
    if args.action in {'prepare', 'reboot'}:
        # Never expose JSON parser failures (which may quote invalid secret data).
        try:
            raw = sys.stdin.buffer.read(32769)
            if len(raw) > 32768:
                raise ValueError()
            data = json.loads(raw)
            if set(data) != {'sudo_password'}:
                raise ValueError()
            password = data['sudo_password']
        except (ValueError, TypeError):
            raise ValueError('invalid credential input envelope') from None
    store = Store(paths.state / 'setup.sqlite3')
    recovery = Recovery(paths, store, password=password)
    matches = [s for s in store.list_setups(limit=1000) if s.plan_digest == args.digest]
    # Read-only snapshots must remain available while a detached installer owns
    # the lock. Only setup creation requires exclusive ownership here.
    if args.action == 'prepare' and not matches:
        from h3server.installer.runner import Installer
        runner = Installer(paths, store)
        with runner.lock():
            matches = [s for s in store.list_setups(limit=1000) if s.plan_digest == args.digest]
            if not matches:
                plan = json.loads((stage / 'plan.json').read_text())
                approval = Approval(**json.loads((stage / 'approval.json').read_text()))
                validate_approval(plan, approval)
                if canonical_plan_digest(plan) != args.digest:
                    raise ApprovalRequired('stale staged plan')
                matches = [runner.create(plan)]
    if len(matches) != 1:
        raise ApprovalRequired('expected exactly one setup for approved digest')
    setup = matches[0]
    if args.action in {'prepare', 'reboot'}:
        approval = Approval(**json.loads((stage / 'approval.json').read_text()))
        validate_approval(setup.plan, approval)
    if args.detach and args.action == 'snapshot':
        raise ValueError('snapshot cannot detach')
    if args.detach and not _detach():
        return  # launcher acceptance only, not successful setup or reboot
    try:
        if args.action == 'prepare':
            recovery.prepare(setup.setup_id, approval, service_digest=args.service_digest)
        elif args.action == 'reboot':
            recovery.request_reboot(setup.setup_id, approval)
        elif args.action == 'resume':
            recovery.resume(setup.setup_id, args.digest)
        else:
            print(json.dumps(recovery.snapshot(after=args.after)))
    except Exception:
        store.append_event('setup', setup.setup_id, Event('recovery', StageStatus.FAILED,
                           'Recovery action failed; inspect checkpoint, reauthenticate or obtain administrator review.'))
        raise


if __name__ == '__main__':
    main()
