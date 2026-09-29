"""Controller deployment composition; no credentials, server.txt, or source-tree bundling.

Launcher contract: construct ReleaseBundle(wheel_path, trusted_sha256) and pass it
as release= to create_app. A release channel/operator must authenticate that pin;
we do not mistake a locally calculated digest for release provenance. Package
resources include web/*, backends/manifest.json and backends/LICENSE.h3.txt.
"""
from dataclasses import asdict
import hashlib
import io
from importlib.resources import files
import json
from pathlib import Path, PurePosixPath
import re
import stat
import uuid
import zipfile

from h3server.backends import get_profile, load_manifest, load_profiles
from h3server.backends.catalog import PRUNED_PROFILE_NAMES, UNAVAILABLE_VARIANTS, get_artifact_set
from h3server.profiles import gpu_family_eligible
from h3server.controller.ssh import ControllerError
from h3server.installer.planner import Approval, ReleaseArtifact, build_plan, validate_approval, validate_reboot_approval
from h3server.store import ApprovalRequired, canonical_plan_digest


class ReleaseBundle:
    def __init__(self, wheel, expected_sha256):
        self.path = Path(wheel)
        if self.path.is_symlink() or not self.path.is_file() or self.path.stat().st_size > 64 * 1024**2:
            raise ValueError('expected bounded release wheel')
        self.data = self.path.read_bytes()
        digest = hashlib.sha256(self.data).hexdigest()
        if expected_sha256 != digest:
            raise ValueError('release digest mismatch')
        self.artifact = ReleaseArtifact('sha256-' + digest, self.path.name, digest)
        with zipfile.ZipFile(io.BytesIO(self.data)) as archive:
            names = archive.namelist()
            if len(names) != len(set(names)) or sum(i.file_size for i in archive.infolist()) > 128 * 1024**2:
                raise ValueError('invalid release archive')
            for info in archive.infolist():
                parts = PurePosixPath(info.filename).parts
                if (not parts or info.filename.startswith('/') or '\\' in info.filename or '..' in parts
                        or not (parts[0] == 'h3server' or re.fullmatch(r'h3server-[A-Za-z0-9_.+-]+\.dist-info', parts[0]))
                        or any(p.startswith('.') or p in {'tests', 'node_modules', '__pycache__', 'venv', 'build', 'server.txt'} for p in parts)
                        or stat.S_ISLNK(info.external_attr >> 16) or info.filename.endswith(('.pyc', '.pem', '.key'))):
                    raise ValueError('release contains non-distribution content')
            if 'h3server/__init__.py' not in names:
                raise ValueError('missing application package')


def profile_document(name, hardware=None):
    """JSON presentation of the exact catalog selection, not an approval or fit claim."""
    profile = get_profile(name)
    if name in PRUNED_PROFILE_NAMES:
        selected = get_artifact_set(name)
        return {**asdict(selected), 'artifacts': [{**asdict(a), 'url': a.url} for a in selected.artifacts],
                'artifact_set_digest': selected.digest, 'weight_bytes': selected.weight_bytes,
                'tokenizer_bytes': selected.tokenizer_bytes, 'selected_bytes': selected.selected_bytes,
                'runtime_bytes': selected.runtime_bytes, 'peak_weight_disk_bytes': selected.peak_weight_disk_bytes,
                'gpu_eligible': (gpu_family_eligible(hardware, selected.required_gpu_family)
                                 if hardware is not None and selected.required_gpu_family else None)}
    return {'profile_name': name, 'label': 'Legacy ' + ('BF16 CPU offload' if 'bf16' in name else 'INT8 group offload'),
            'runtime_id': profile.runtime_id or 'diffusers-minimax-h3-t2va', 'artifact_set_digest': None,
            'weight_bytes': load_manifest()['model']['total_bytes'], 'artifacts': [],
            'license_disclosures': [], 'memory_status': 'legacy research budgets; not measured on this host'}


def profile_catalog(hardware=None):
    return {'profiles': [profile_document(p.name, hardware) for p in load_profiles(include_pruned=True)],
            'license_text': license_document(), 'license_sha256': load_manifest()['sources']['license']['sha256'],
            'unavailable': [{'profile_name': name, 'reason': reason} for name, reason in UNAVAILABLE_VARIANTS]}


def license_document():
    data = files('h3server.backends').joinpath('LICENSE.h3.txt').read_bytes()
    if hashlib.sha256(data).hexdigest() != load_manifest()['sources']['license']['sha256']:
        raise ValueError('packaged license mismatch')
    return data.decode('utf-8')


def _directory(sftp, path):
    """Reject remote symlink traversal, including ancestors. Private owned tree."""
    if not path.startswith('/') or any(part in {'.', '..'} for part in path.split('/')):
        raise ControllerError('unsafe_remote_path')
    current = ''
    for part in PurePosixPath(path).parts[1:]:
        current += '/' + part
        try:
            info = sftp.lstat(current)
        except FileNotFoundError:
            sftp.mkdir(current, 0o700)
            info = sftp.lstat(current)
        if not stat.S_ISDIR(info.st_mode):
            raise ControllerError('unsafe_remote_path')
    sftp.chmod(path, 0o700)


def _read(sftp, path, limit=1024 * 1024):
    info = sftp.lstat(path)
    if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077 or info.st_size > limit:
        raise ControllerError('unsafe_remote_file')
    with sftp.open(path, 'rb') as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ControllerError('remote_file_too_large')
    return data


def _write(sftp, path, data, *, immutable=False):
    _directory(sftp, str(PurePosixPath(path).parent))
    try:
        old = _read(sftp, path, max(len(data), 1024 * 1024))
    except FileNotFoundError:
        old = None
    if old == data:
        return
    if old is not None and immutable:
        raise ControllerError('release_collision')
    temporary = path + '.' + uuid.uuid4().hex
    try:
        with sftp.open(temporary, 'wx') as stream:
            sftp.chmod(temporary, 0o600)
            stream.write(data)
            stream.flush()
        if hashlib.sha256(_read(sftp, temporary, len(data))).digest() != hashlib.sha256(data).digest():
            raise ControllerError('staging_integrity_failed')
        if old is None:
            sftp.rename(temporary, path)
        else:
            sftp.posix_rename(temporary, path)
    finally:
        try:
            sftp.remove(temporary)
        except FileNotFoundError:
            pass


class Deployment:
    def __init__(self, connection, release=None):
        self.connection, self.release = connection, release
        self.plan = self.service = None
        self.root = None

    def reset(self):
        self.plan = self.service = self.root = None

    def _authenticated(self):
        if self.connection.status()['state'] != 'authenticated':
            raise ControllerError('authentication_required')
        if self.release is None:
            raise ControllerError('release_unavailable')
        root = self.connection.remote_home().rstrip('/') + '/MinimaxH3Server'
        if self.root is not None and self.root != root:
            self.reset()
        self.root = root

    def _stage(self, approval=None, *, publish=True):
        with self.connection.private_sftp() as sftp:
            _directory(sftp, self.root + "/state")
        with self.connection.staging_transaction(self.root) as check:
            self._stage_locked(approval, publish=publish, check=check)

    def _require_staged_binding(self, sftp):
        # A controller waiting for the staging lock may hold a superseded plan.
        # It must not publish approvals/launch evidence after another Install
        # replaced that discovery pointer.
        try:
            saved = json.loads(_read(sftp, self.root + '/config/deployment.json'))
        except (OSError, ValueError):
            raise ControllerError('selection_changed') from None
        if saved != {'artifact': asdict(self.release.artifact), 'digest': canonical_plan_digest(self.plan)}:
            raise ControllerError('selection_changed')

    def _stage_locked(self, approval=None, *, publish=True, check):
        with self.connection.private_sftp() as sftp:
            if approval is not None:
                self._require_staged_binding(sftp)
            _directory(sftp, self.root)
            _directory(sftp, self.root + '/state')
            artifact = self.release.artifact
            check()
            _write(sftp, f'{self.root}/releases/{artifact.release_id}/{artifact.filename}', self.release.data, immutable=True)
            check()
            _write(sftp, f'{self.root}/releases/{artifact.release_id}/daemon.json',
                   json.dumps(self.plan['deployment_config'], sort_keys=True).encode(), immutable=True)
            digest = canonical_plan_digest(self.plan)
            check()
            _write(sftp, f'{self.root}/bootstrap/{digest}/plan.json', json.dumps(self.plan, sort_keys=True).encode(), immutable=True)
            if approval is not None:
                check()
                _write(sftp, f'{self.root}/bootstrap/{digest}/approval.json', json.dumps(asdict(approval), sort_keys=True).encode())
            if publish:
                check()
                _write(sftp, f'{self.root}/config/deployment.json', json.dumps({'artifact': asdict(artifact), 'digest': digest}).encode())

    def make_plan(self, profile, *, sudo_password_available=False):
        with self.connection.lock:
            self._authenticated()
            inspection = self.connection.inspect_install(sudo_password_available=sudo_password_available)
            self.plan = build_plan(inspection, profile=profile, artifact=self.release.artifact)
            self.plan['deployment_config'] = {'port': 8765, 'root': self.root}
            self.service = None
            # This action is explicitly labelled staging in UI, not a read-only scan.
            self._stage()
            if self.plan['purpose'] == 'environment' and inspection.python_ready:
                result = self.connection.recovery(root=self.root, artifact=self.release.artifact,
                    digest=canonical_plan_digest(self.plan), action='service-plan')
                if not result.succeeded:
                    raise ControllerError('service_plan_unavailable', 502)
                self.service = json.loads(result.stdout)
            return self.document()

    def rediscover_stale_plan(self, profile):
        """Explicit Install only: replace a proven unlaunched discovery pointer.

        The low-level restore/recovery exact-release guard is deliberately intact.
        Neither proving eligibility nor reading a SQLite copy runs target code.
        """
        from h3server.controller.discovery import ERROR, record, prove_unlaunched
        with self.connection.lock:
            self._authenticated()
            with self.connection.private_sftp() as sftp:
                try:
                    raw, saved, artifact = record(sftp, self.root)
                except FileNotFoundError:
                    return None
                except (ValueError, TypeError, KeyError, ControllerError):
                    raise ControllerError(ERROR) from None
                if artifact == self.release.artifact:
                    return None
                # Fail without even creating synchronization files for unsafe work.
                proof = prove_unlaunched(sftp, self.root, raw)
            with self.connection.staging_transaction(self.root, discovery=True) as check:
                with self.connection.private_sftp() as sftp:
                    if prove_unlaunched(sftp, self.root, raw) != proof:
                        raise ControllerError(ERROR)
                inspection = self.connection.inspect_install(sudo_password_available=True)
                plan = build_plan(inspection, profile=profile, artifact=self.release.artifact)
                plan['deployment_config'] = {'port': 8765, 'root': self.root}
                with self.connection.private_sftp() as sftp:
                    if prove_unlaunched(sftp, self.root, raw) != proof:
                        raise ControllerError(ERROR)
                old_plan, old_service = self.plan, self.service
                self.plan, self.service = plan, None
                try:
                    self._stage(publish=False)
                    with self.connection.private_sftp() as sftp:
                        after = prove_unlaunched(sftp, self.root, raw)
                        # The only added evidence may be our new immutable plan.
                        records = dict(proof[2])
                        records[canonical_plan_digest(plan)] = {'plan.json': json.dumps(plan, sort_keys=True).encode()}
                        if after != (proof[0], proof[1], records):
                            raise ControllerError(ERROR)
                        check()
                        _write(sftp, self.root + '/config/deployment.json', json.dumps({
                            'artifact': asdict(self.release.artifact), 'digest': canonical_plan_digest(plan)}).encode())
                except Exception:
                    self.plan, self.service = old_plan, old_service
                    raise
            if plan['purpose'] == 'environment' and inspection.python_ready:
                result = self.connection.recovery(root=self.root, artifact=self.release.artifact,
                    digest=canonical_plan_digest(plan), action='service-plan')
                if not result.succeeded:
                    raise ControllerError('service_plan_unavailable', 502)
                self.service = json.loads(result.stdout)
            return self.document()

    def document(self):
        return {'plan': self.plan, 'plan_digest': canonical_plan_digest(self.plan),
                'service': self.service, 'service_digest': canonical_plan_digest(self.service) if self.service else None,
                'license_text': license_document(), 'license_sha256': load_manifest()['sources']['license']['sha256'],
                'selection': profile_document(self.plan['profile'])}

    def can_rediscover_blocked_plan(self, snapshot):
        """Only a blocked discovery with no durable launch evidence may be replaced."""
        with self.connection.lock:
            self._authenticated()
            if self.plan is None or not self.plan['blockers']:
                return False
            digest = canonical_plan_digest(self.plan)
            if (snapshot.get('operation') or {}).get('plan_digest') == digest:
                return False
            if (snapshot.get('active_release') or {}).get('environment') == digest:
                return False
            for checkpoint in snapshot['setups']:
                if checkpoint['plan_digest'] == digest:
                    # Prerequisite snapshots synthesize awaiting_approval when no
                    # status file exists. All real checkpoints prevent discovery.
                    if self.plan['purpose'] != 'prerequisites' or checkpoint['state'] != 'awaiting_approval':
                        return False
            with self.connection.private_sftp() as sftp:
                for name in ('composite-install.json', 'approval.json', 'status'):
                    try:
                        sftp.lstat(f'{self.root}/bootstrap/{digest}/{name}')
                    except FileNotFoundError:
                        continue
                    return False  # partial records and symlinks also block retry
            return True

    def install_once(self, approval, *, service_digest, sudo_password=None):
        """Composite-only durable at-most-once launch guard, including lost replies.

        Exclusive creation precedes mutation. An incomplete/ambiguous launch must
        be observed or explicitly repaired via the low-level APIs, never replayed
        by another Install click or a restarted controller. No credential is saved.
        """
        with self.connection.lock:
            self._authenticated()
            if self.plan is None:
                raise ControllerError('plan_required')
            try:
                validate_approval(self.plan, approval)
            except (ValueError, ApprovalRequired):
                raise ControllerError('approval_required') from None
            if self.plan['purpose'] == 'environment' and (not self.service or approval.privileged is not True or service_digest != canonical_plan_digest(self.service)):
                raise ControllerError('approval_required')
            with self.connection.staging_transaction(self.root) as check:
                path = f'{self.root}/bootstrap/{approval.plan_digest}/composite-install.json'
                with self.connection.private_sftp() as sftp:
                    self._require_staged_binding(sftp)
                    _directory(sftp, str(PurePosixPath(path).parent))
                    try:
                        sftp.lstat(path)
                    except FileNotFoundError:
                        pass
                    else:
                        raise ControllerError('setup_operation_outcome_unknown')
                    # Unlike replacement staging, wx is exclusive across controllers.
                    # A partial marker also blocks replay, deliberately failing closed.
                    check()
                    with sftp.open(path, 'wx') as stream:
                        sftp.chmod(path, 0o600)
                        stream.write(json.dumps({'plan_digest': approval.plan_digest}).encode())
                        stream.flush()
            # The durable marker now prevents rediscovery. Do not hold the
            # short staging guardian across potentially hour-long prerequisites.
            return self.install(approval, service_digest=service_digest, sudo_password=sudo_password, replan=False)

    def install(self, approval, *, service_digest, sudo_password=None, replan=True):
        with self.connection.lock:
            self._authenticated()
            if self.plan is None:
                raise ControllerError('plan_required')
            try:
                validate_approval(self.plan, approval)
                if self.plan['purpose'] == 'environment' and (not self.service or approval.privileged is not True or service_digest != canonical_plan_digest(self.service)):
                    raise ApprovalRequired('separate service approval required')
            except (ValueError, ApprovalRequired):
                raise ControllerError('approval_required') from None
            # Check before either installation path, without requiring Python:
            # prerequisite discovery may be repairing a live deployment's Python.
            with self.connection.private_sftp() as sftp:
                try:
                    _read(sftp, self.root + '/state/active-release.json')
                    active_release = True
                except FileNotFoundError:
                    active_release = False
            if active_release and (self.plan['privileged_required'] or self.plan['reboot_required']):
                raise ControllerError('update_prerequisite_maintenance_required')
            self._stage(approval)
            if self.plan['purpose'] == 'prerequisites':
                self.connection.install_prerequisites(root=self.root, plan=self.plan, approval=approval, sudo_password=sudo_password)
                # The composite coordinator budgets discovery itself. Low-level
                # callers retain their fresh-review response and explicit approval.
                if not replan:
                    return {'state': 'waiting_for_replan'}
                return {'state': 'waiting_for_replan', 'review': self.make_plan(self.plan['profile'], sudo_password_available=sudo_password is not None)}
            if active_release:
                return self.connection.deployment_action(root=self.root, artifact=self.release.artifact,
                    digest=approval.plan_digest, action='install-update', data={'plan_digest': approval.plan_digest})
            result = self.connection.recovery(root=self.root, artifact=self.release.artifact,
                digest=approval.plan_digest, action='prepare', service_digest=service_digest, sudo_password=sudo_password)
            if not result.succeeded:
                raise ControllerError('installer_launch_failed', 502)
            return {'state': 'accepted', 'installation_ready': False, 'model_ready': False, 'generation_tested': False}

    def restore(self, *, expected_digest=None, expected_root=None, observation_only=False):
        """Discover authenticated state; automatic recovery may only observe its binding."""
        with self.connection.lock:
            self._authenticated()
            if expected_root is not None and self.root != expected_root:
                raise ControllerError('recovery_binding_changed')
            with self.connection.private_sftp() as sftp:
                record = json.loads(_read(sftp, self.root + '/config/deployment.json'))
                artifact = ReleaseArtifact(**record['artifact'])
                # Never execute a release just because remote state names it.
                if artifact != self.release.artifact:
                    raise ControllerError('matching_release_required')
                if not re.fullmatch('[a-f0-9]{64}', record['digest']):
                    raise ControllerError('invalid_checkpoint')
                if expected_digest is not None and record['digest'] != expected_digest:
                    raise ControllerError('recovery_binding_changed')
                plan = json.loads(_read(sftp, f"{self.root}/bootstrap/{record['digest']}/plan.json"))
                if canonical_plan_digest(plan) != record['digest']:
                    raise ControllerError('invalid_checkpoint')
                self.plan = plan
            self.service = None
            if not observation_only and self.plan['purpose'] == 'prerequisites' and self.snapshot()['setups'][0]['state'] == 'waiting_for_replan':
                return self.make_plan(self.plan['profile'])
            if self.plan['purpose'] == 'environment':
                result = self.connection.recovery(root=self.root, artifact=artifact, digest=record['digest'], action='service-plan')
                if not result.succeeded:
                    raise ControllerError('service_plan_unavailable', 502)
                self.service = json.loads(result.stdout)
            return self.document()

    def snapshot(self, after=0):
        with self.connection.lock:
            self._authenticated()
            if self.plan is None:
                self.restore()
            digest = canonical_plan_digest(self.plan)
            if self.plan['purpose'] == 'prerequisites':
                # The fixed shell guardian is authoritative before Python exists.
                # These are durable statuses, never inferred percentage/completion.
                with self.connection.private_sftp() as sftp:
                    try:
                        state = _read(sftp, f'{self.root}/bootstrap/{digest}/status', 64).decode().strip()
                    except FileNotFoundError:
                        state = 'awaiting_approval'
                if state not in {'awaiting_approval', 'running', 'failed', 'waiting_for_replan'}:
                    raise ControllerError('invalid_checkpoint')
                return {'setups': [{'plan_digest': digest, 'state': state}], 'events': [], 'cursor': 0,
                        'has_more': False, 'active_release': None, 'operation': None}
            return self.connection.deployment_action(root=self.root, artifact=self.release.artifact,
                digest=digest, action='snapshot', data={'after': after})

    def action(self, action, data):
        with self.connection.lock:
            self._authenticated()
            if self.plan is None:
                self.restore()
            digest = canonical_plan_digest(self.plan)
            if data.get('plan_digest') != digest:
                raise ControllerError('approval_required')
            if action == 'reboot':
                approval = Approval(**{k: data[k] for k in asdict(Approval(digest))})
                try:
                    validate_reboot_approval(self.plan, approval)
                except (ValueError, ApprovalRequired):
                    raise ControllerError('approval_required') from None
                self._stage(approval)
                result = self.connection.recovery(root=self.root, artifact=self.release.artifact, digest=digest,
                    action='reboot', sudo_password=data.get('sudo_password'))
                if not result.succeeded:
                    raise ControllerError('reboot_launch_failed', 502)
                return {'state': 'accepted', 'reauthentication_required': True}
            if action == 'resume':
                if self.plan['purpose'] == 'prerequisites':
                    raise ControllerError('fresh_prerequisite_approval_required')
                snapshot = self.snapshot()
                if snapshot.get('active_release'):
                    return self.connection.deployment_action(root=self.root, artifact=self.release.artifact,
                        digest=digest, action='install-update', data={'plan_digest': digest})
                result = self.connection.recovery(root=self.root, artifact=self.release.artifact, digest=digest, action='resume')
                if not result.succeeded:
                    raise ControllerError('resume_launch_failed', 502)
                return {'state': 'accepted'}
            return self.connection.deployment_action(root=self.root, artifact=self.release.artifact, digest=digest, action=action, data=data)


def main(argv=None):
    """Backward-compatible source entry point for the native launcher."""
    from h3server.__main__ import main as launch
    return launch(argv)


if __name__ == '__main__':
    raise SystemExit(main())
