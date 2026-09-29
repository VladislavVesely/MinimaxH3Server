"""OPT-IN destructive acceptance on the Linux TARGET, not the local controller.

Run with the installed target environment Python. Never downloads weights or
installs packages. Every mutating mode requires a typed hostname-specific consent.
Reboot additionally requires --disposable-vm and a distinct confirmation. Do not
run on production. See docs/operations.md for prerequisites and evidence limits.
"""
import argparse
import hashlib
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import uuid


def confirmation(action, disposable=False):
    if action == 'reboot' and not disposable:
        raise SystemExit('reboot requires --disposable-vm and separate confirmation')
    phrase = ('REBOOT DISPOSABLE VM' if action == 'reboot' else 'ACCEPT ' + action.upper()) + ' ' + socket.gethostname()
    if input('Type exactly "' + phrase + '" to authorize target mutation: ') != phrase:
        raise SystemExit('not approved; no target changes')


def download(paths, job_id, destination):
    from h3server.backends.base import regular_file
    with regular_file(paths, 'config/remote-token') as stream:
        token = stream.read(258).decode('ascii').strip()
    connection = HTTPConnection('127.0.0.1', 8765, timeout=60)
    try:
        connection.request('GET', f'/api/v1/jobs/{job_id}/video?download=true', headers={'Authorization': 'Bearer ' + token})
        response = connection.getresponse()
        if response.status != 200 or response.getheader('Content-Type') != 'video/mp4':
            raise RuntimeError('completed video download failed')
        digest = hashlib.sha256()
        size = 0
        with destination.open('xb') as output:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > 4 * 1024**3:
                    raise RuntimeError('download exceeds acceptance limit')
                output.write(chunk)
                digest.update(chunk)
        return {'bytes': size, 'sha256': digest.hexdigest(), 'path': str(destination)}
    finally:
        connection.close()


def generation(paths, io, profile, timeout):
    from h3server.backends import load_manifest
    health = io.api('GET', 'health')
    if (not health['runtime_ready'] or health['diagnostic'].get('backend_revision') != load_manifest()['backend']['revision']):
        raise RuntimeError('real pinned H3 runtime/model readiness required; no fixture fallback')
    job = io.api('POST', 'jobs', {'prompt': 'A red paper boat floating on a calm pond, natural daylight.',
                                'profile': profile, 'num_frames': 124, 'width': 960, 'height': 544, 'steps': 50, 'seed': 42})
    job_id = job['job_id']
    print('Real generation submitted: ' + job_id, flush=True)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = io.api('GET', 'jobs/' + job_id)
        if job['status'] == 'completed':
            break
        if job['status'] in {'failed', 'cancelled', 'interrupted'}:
            raise RuntimeError('real generation did not complete: ' + job['status'])
        time.sleep(2)
    else:
        raise RuntimeError('generation deadline exceeded; job remains visible, cancel explicitly in the UI')
    result = download(paths, job_id, paths.state / ('acceptance-' + job_id + '.mp4'))
    import av  # Already required on the real target; never installs a codec/runtime here.
    with av.open(result['path']) as media:
        if not media.streams.video or not media.streams.audio:
            raise RuntimeError('expected playable video and audio streams')
        frame = next(media.decode(video=0), None)
        if frame is None or frame.width != 960 or frame.height != 544:
            raise RuntimeError('downloaded video did not decode at requested dimensions')
    with av.open(result['path']) as media:
        if next(media.decode(audio=0), None) is None:
            raise RuntimeError('downloaded audio did not decode')
    return {'job_id': job_id, 'download': result, 'decoded_video_and_audio': True,
            'manual_browser_playback_required': True}


def rollback(paths, digest):
    from h3server.backends.base import read_json
    from h3server.controller.target import Activation, TargetIO, active_release, operation_lock
    from h3server.installer.planner import Approval, validate_approval
    from h3server.installer.runner import Installer
    from h3server.store import Store, canonical_plan_digest
    plan = read_json(paths, f'bootstrap/{digest}/plan.json')
    if canonical_plan_digest(plan) != digest:
        raise RuntimeError('plan digest mismatch')
    approval = Approval(**read_json(paths, f'bootstrap/{digest}/approval.json'))
    validate_approval(plan, approval)
    previous = active_release(paths)
    if previous is None or previous['environment'] == digest:
        raise RuntimeError('a distinct installed candidate and healthy previous release are required')
    class FailCandidateHealthOnce(TargetIO):
        injected = False
        def health(self):
            # Real restart/switch, then deliberately exercise real rollback.
            if not self.injected:
                self.injected = True
                raise RuntimeError('acceptance: intentionally failed candidate health')
            return super().health()
    io = FailCandidateHealthOnce(paths)
    with operation_lock(paths), Installer(paths, Store(paths.state / 'setup.sqlite3')).lock():
        try:
            Activation(paths, io=io).run(plan, uuid.uuid4().hex, policy='reject')
        except RuntimeError:
            if not io.injected:
                raise
        journal = read_json(paths, 'state/activation.json')
        if journal['phase'] != 'rolled_back' or active_release(paths) != previous:
            raise RuntimeError('rollback not complete; use explicit activation repair')
        io.health()
        if io.api('GET', 'maintenance')['active']:
            raise RuntimeError('rollback left maintenance gate active')
    return {'rollback': 'passed', 'previous_release': previous['release'], 'injected_candidate_health_failure': True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['restart', 'generation', 'rollback', 'reboot', 'verify-reboot'])
    parser.add_argument('--root', type=Path, default=Path.home() / 'MinimaxH3Server')
    parser.add_argument('--profile', choices=['h3-bf16-cpu-offload-experimental', 'h3-int8-group-offload-experimental'], default='h3-bf16-cpu-offload-experimental')
    parser.add_argument('--candidate-digest')
    parser.add_argument('--timeout', type=int, default=21600)
    parser.add_argument('--disposable-vm', action='store_true')
    args = parser.parse_args(argv)
    if sys.platform != 'linux':
        raise SystemExit('acceptance must run explicitly on the Linux target; no SSH automation')
    if os.geteuid() == 0:
        raise SystemExit('run as the unprivileged SSH/application owner, never root')
    if args.timeout < 1 or args.timeout > 86400:
        raise SystemExit('timeout must be 1..86400 seconds')
    if args.action == 'rollback':
        import re
        if not re.fullmatch('[a-f0-9]{64}', args.candidate_digest or ''):
            raise SystemExit('rollback requires --candidate-digest for an installed approved candidate')
    if args.action != 'verify-reboot':
        confirmation(args.action, args.disposable_vm)
    os.umask(0o077)
    from h3server.paths import AppPaths
    from h3server.controller.target import TargetIO
    from h3server.backends.base import atomic_json, read_json
    paths = AppPaths(args.root)
    io = TargetIO(paths)
    io.health()
    if args.action == 'restart':
        operation = uuid.uuid4().hex
        io.api('POST', 'maintenance/begin', {'operation': operation, 'policy': 'reject'})
        io.service(['restart', 'h3server.service'])
        io.health()
        if io.api('GET', 'maintenance')['operation'] != operation:
            raise RuntimeError('durable maintenance ownership was lost on restart')
        io.api('POST', 'maintenance/end', {'operation': operation})
        result = {'restart': 'passed', 'durable_maintenance': True}
    elif args.action == 'generation':
        result = generation(paths, io, args.profile, args.timeout)
    elif args.action == 'rollback':
        result = rollback(paths, args.candidate_digest)
    elif args.action == 'reboot':
        if not io.api('GET', 'maintenance')['idle']:
            raise RuntimeError('reboot acceptance requires idle jobs')
        atomic_json(paths, 'state/acceptance-reboot.json', {'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                    'time': time.time(), 'hostname': socket.gethostname()})
        print('Checkpoint saved. After reconnecting, run verify-reboot. Sudo prompts locally; no password stored.', flush=True)
        subprocess.run(['sudo', '--', 'reboot'], check=True)
        return 0
    else:
        checkpoint = read_json(paths, 'state/acceptance-reboot.json')
        if checkpoint['hostname'] != socket.gethostname() or checkpoint['boot_id'] == Path('/proc/sys/kernel/random/boot_id').read_text().strip():
            raise RuntimeError('no new boot on the checkpointed VM')
        result = {'reboot': 'new boot with authenticated daemon healthy', 'installer_resume_not_certified': True}
    atomic_json(paths, 'state/acceptance-' + args.action + '.json', {**result, 'time': time.time()})
    print(json.dumps(result, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
