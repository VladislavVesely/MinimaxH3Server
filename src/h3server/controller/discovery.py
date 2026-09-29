"""Proof for replacing *unlaunched discovery*, never a checkpoint migration.

No remote wheel is imported. SQLite is inspected locally from a bounded, private
copy only while the staging/installer/deployment locks are held. Sidecars and
unknown evidence fail closed. Old controllers without the staging lock must be
stopped for this one-time handoff; a final comparison is not mixed-version race
exclusion.
"""
from dataclasses import asdict
import json
from pathlib import PurePosixPath
import re
import shlex
import sqlite3
import stat
import tempfile

from h3server.controller.ssh import ControllerError
from h3server.installer.planner import Approval, ReleaseArtifact, validate_approval, validate_plan
from h3server.store import canonical_plan_digest, _SCHEMA, _SCHEMA_VERSION


ERROR = 'stale_discovery_unproven'


def staging_command(root, *, discovery=False, nonce=''):
    """Fixed shell guardian; stdin EOF/loss releases every lock, no remote Python.

    Global lock order: staging -> installer -> deployment. Normal staging takes
    only staging; it never waits for installer/deployment while holding either.
    All acquisitions are nonblocking. A remote 240s watchdog also bounds a
    half-open SSH holder, beyond the controller's 180s staging deadline.
    """
    paths = [root + '/state/' + name + '.lock' for name in
             (('staging', 'installer', 'deployment') if discovery else ('staging',))]
    commands = ['umask 077']
    for fd, path in enumerate(paths, 7):
        q = shlex.quote(path)
        commands += [f'test ! -L {q}', f'test ! -e {q} || test -f {q}',
                     f'exec {fd}<>{q}', f'test ! -L {q}',
                     f'test {q} -ef /dev/fd/{fd}', f'flock -n {fd}']
    commands += ["printf '%s\\n' " + shlex.quote('H3READY ' + nonce),
                 'IFS= read -r reply', 'test "$reply" = finish']
    script = ' && '.join('(' + c + ')' if ' || ' in c else c for c in commands)
    return 'exec timeout --signal=TERM --kill-after=5 240 sh -c ' + shlex.quote(script)


def directory(sftp, path):
    """Read-only counterpart to staging's mkdir/chmod; reject every symlink."""
    current = ''
    for part in PurePosixPath(path).parts[1:]:
        if part in {'.', '..'}:
            raise ValueError('unsafe directory')
        current += '/' + part
        if not stat.S_ISDIR(sftp.lstat(current).st_mode):
            raise ValueError('unsafe directory')
    names = sftp.listdir(path)
    if len(names) > 128:
        raise ValueError('unbounded discovery directory')
    return set(names)


def empty_tree(sftp, path, *, budget, depth=0):
    budget[0] -= 1
    if depth > 8 or budget[0] < 0:
        raise ValueError('unbounded directory tree')
    for name in directory(sftp, path):
        empty_tree(sftp, path + '/' + name, budget=budget, depth=depth + 1)


def strict_json(data):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key')
            result[key] = value
        return result
    return json.loads(data, object_pairs_hook=pairs)


def record(sftp, root):
    from h3server.controller.deploy import _read
    directory(sftp, root + '/config')
    raw = _read(sftp, root + '/config/deployment.json')
    data = strict_json(raw)
    if set(data) != {'artifact', 'digest'} or not re.fullmatch('[a-f0-9]{64}', data['digest']):
        raise ValueError('invalid discovery pointer')
    return raw, data, ReleaseArtifact(**data['artifact'])


def empty_database(data):
    # Never execute schema-owned functions/triggers or use the application's Store
    # constructor (which creates/migrates state). A sidecar-free snapshot is required.
    with tempfile.TemporaryDirectory(prefix='h3-discovery-') as temporary:
        from pathlib import Path
        path = Path(temporary) / 'setup.sqlite3'
        path.write_bytes(data)
        db = sqlite3.connect(path.as_uri() + '?mode=ro&immutable=1', uri=True)
        try:
            db.execute('PRAGMA trusted_schema=OFF')
            schema = db.execute("SELECT type,name,sql FROM sqlite_master WHERE name NOT GLOB 'sqlite_*'").fetchall()
            if (any(kind != 'table' for kind, _, _ in schema)
                    or {name: sql for _, name, sql in schema} != _SCHEMA
                    or db.execute('PRAGMA user_version').fetchone()[0] != _SCHEMA_VERSION):
                raise ValueError('unknown database schema')
            if db.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise ValueError('invalid database')
            for table in ('setups', 'jobs', 'events'):
                if db.execute('SELECT 1 FROM ' + table + ' LIMIT 1').fetchone():
                    raise ValueError('durable work exists')
            rows = db.execute('SELECT singleton,last_id,minimum_cursor FROM event_state').fetchall()
            if rows != [(1, 0, 0)]:
                raise ValueError('invalid initial event replay metadata')
            # Match Store's empty-log sequence invariant without constructing or
            # migrating Store: no sequence yet, or exactly one initial zero.
            sequence = db.execute("SELECT seq FROM sqlite_sequence WHERE name='events'").fetchall()
            if sequence not in ([], [(0,)]):
                raise ValueError('invalid initial event sequence')
        finally:
            db.close()


def prove_unlaunched(sftp, root, expected):
    """Return a byte-bound proof; any unfamiliar file/evidence blocks replacement."""
    from h3server.controller.deploy import _read
    try:
        raw, saved, artifact = record(sftp, root)
        if raw != expected:
            raise ValueError('discovery pointer changed')
        root_names = directory(sftp, root)
        directory(sftp, root + '/releases/' + artifact.release_id)
        allowed = {'bootstrap', 'releases', 'config', 'state'}
        budget = [128]
        for name in root_names - allowed:
            # Empty application directories are harmless; links/files are not.
            empty_tree(sftp, root + '/' + name, budget=budget)
        if directory(sftp, root + '/config') != {'deployment.json'}:
            raise ValueError('configuration evidence exists')
        states = directory(sftp, root + '/state')
        if states - {'staging.lock', 'installer.lock', 'deployment.lock', 'setup.sqlite3'}:
            raise ValueError('state evidence exists')
        for name in states:
            _read(sftp, root + '/state/' + name, 16 * 1024**2 if name == 'setup.sqlite3' else 1024)
        database = _read(sftp, root + '/state/setup.sqlite3', 16 * 1024**2) if 'setup.sqlite3' in states else None
        if database is not None:
            empty_database(database)
        stages = directory(sftp, root + '/bootstrap')
        if saved['digest'] not in stages:
            raise ValueError('missing discovery')
        records = {}
        for digest in stages:
            if not re.fullmatch('[a-f0-9]{64}', digest):
                raise ValueError('invalid bootstrap directory')
            stage = root + '/bootstrap/' + digest
            names = directory(sftp, stage)
            data = _read(sftp, stage + '/plan.json')
            plan = strict_json(data)
            validate_plan(plan)
            if canonical_plan_digest(plan) != digest or plan.get('deployment_config') != {'port': 8765, 'root': root}:
                raise ValueError('invalid staged discovery')
            if digest == saved['digest'] and ReleaseArtifact(**plan['artifact']) != artifact:
                raise ValueError('artifact mismatch')
            evidence = {'plan.json': data}
            if names != {'plan.json'}:
                # Only history may carry completed prerequisite receipts. The
                # current pointer must still identify a never-launched plan.
                required = {'plan.json', 'approval.json', 'status'}
                if (digest == saved['digest'] or plan['purpose'] != 'prerequisites'
                        or plan['reboot_required'] or not required <= names
                        or names - (required | {'composite-install.json'})):
                    raise ValueError('launch evidence exists')
                evidence['approval.json'] = _read(sftp, stage + '/approval.json', 16384)
                approval = strict_json(evidence['approval.json'])
                if (type(approval) is not dict or set(approval) != set(asdict(Approval(digest)))
                        or any(type(value) is not bool for key, value in approval.items() if key != 'plan_digest')):
                    raise ValueError('invalid historical approval')
                validate_approval(plan, Approval(**approval))
                evidence['status'] = _read(sftp, stage + '/status', 64)
                # PrerequisiteProtocol publishes this only on finish after
                # Steps.system_packages succeeds; unfinished/EOF paths fail.
                if evidence['status'] != b'waiting_for_replan\n':
                    raise ValueError('prerequisites not completed')
                if 'composite-install.json' in names:
                    evidence['composite-install.json'] = _read(sftp, stage + '/composite-install.json', 1024)
                    if strict_json(evidence['composite-install.json']) != {'plan_digest': digest}:
                        raise ValueError('invalid historical launch marker')
            records[digest] = evidence
        return (raw, database, records)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, ControllerError):
        raise ControllerError(ERROR) from None
