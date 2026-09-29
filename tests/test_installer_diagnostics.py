"""Real subprocess output persisted and replayed without executing installer packages."""
import io
import json
import os
import sys
import time

import pytest

from h3server.installer.steps import CommandFailure, ProcessIO
from test_installer import prepared


def test_process_failure_is_bounded_sanitized_and_durable(tmp_path):
    from h3server.installer.diagnostics import Diagnostics, read_diagnostics, TAIL_BYTES
    paths, plan, store = prepared(tmp_path)
    setup = store.create_setup(plan)
    log = Diagnostics(paths, setup.setup_id, setup.plan_digest)
    log.phase('dependencies', 'python-packages', 'Install av==18.1.0 / torch CUDA packages')
    script = "print('https://user:PASS@example.org/file?token=SECRET'); print('<script>\\x1b[31m'); print('No matching distribution found for av==18.1.0'); raise SystemExit(1)"
    with pytest.raises(CommandFailure, match='No matching distribution'):
        ProcessIO().run((sys.executable, '-c', script), env=dict(os.environ), log=log)
    record = read_diagnostics(paths, setup)
    assert record['command']['exit_code'] == 1
    assert record['command']['ended_at'] >= record['command']['started_at']
    assert record['command']['last_output_at']
    assert 'av==18.1.0' in record['tail']
    assert all(secret not in json.dumps(record) for secret in ('PASS', 'SECRET', '<script>', '\\u001b', script))
    log.write('é' * TAIL_BYTES)
    assert len(read_diagnostics(paths, setup)['tail'].encode()) <= TAIL_BYTES
    assert (paths.logs / f'installer-{setup.setup_id}.diagnostics.json').stat().st_size < 150000


def test_launch_and_deadline_errors_are_recorded(tmp_path):
    from h3server.installer.diagnostics import Diagnostics, read_diagnostics
    paths, plan, store = prepared(tmp_path)
    setup = store.create_setup(plan)
    log = Diagnostics(paths, setup.setup_id, setup.plan_digest)
    log.phase('dependencies', 'python-packages', 'Python package install')
    with pytest.raises(CommandFailure, match='not found'):
        ProcessIO().run(('/definitely-absent-h3',), env={}, log=log)
    assert read_diagnostics(paths, setup)['command']['error']
    before = time.monotonic()
    with pytest.raises(CommandFailure, match='deadline'):
        ProcessIO(command_timeout=.1).run((sys.executable, '-c', 'import time; time.sleep(20)'), env={}, log=log)
    assert time.monotonic() - before < 3
    assert read_diagnostics(paths, setup)['command']['error'] == 'command deadline exceeded'


def test_diagnostics_reject_symlink_and_legacy_tail_is_confined(tmp_path):
    from h3server.installer.diagnostics import read_diagnostics
    paths, plan, store = prepared(tmp_path)
    setup = store.create_setup(plan)
    file = paths.logs / f'installer-{setup.setup_id}.log'
    file.write_text('No matching distribution found for av==18.1.0\n')
    assert 'av==18.1.0' in read_diagnostics(paths, setup)['tail']
    file.unlink()
    file.symlink_to(tmp_path / 'secret')
    (tmp_path / 'secret').write_text('SECRET')
    with pytest.raises((OSError, ValueError)):
        read_diagnostics(paths, setup)


@pytest.mark.parametrize('text,expected', [
    ('No matching distribution found for av==18.1.0', 'fresh-index'),
    ('ReadTimeoutError: connection timed out', 'transient-download'),
    ('Temporary failure in name resolution', 'transient-download'),
    ('ResolutionImpossible: conflicting dependencies', None),
    ('Permission denied', None),
])
def test_retry_classification(text, expected):
    from h3server.installer.diagnostics import retry_reason
    assert retry_reason(text) == expected


@pytest.mark.parametrize('deadline', [0, -1, float('inf'), float('nan'), 86401])
def test_command_deadline_must_be_finite(deadline):
    with pytest.raises(ValueError):
        ProcessIO(command_timeout=deadline)


def test_closed_output_child_deadline_never_exposes_python_argv():
    script = "import os,time; os.close(1); os.close(2); time.sleep(10) # SECRET-ARGV"
    with pytest.raises(CommandFailure, match='deadline') as failure:
        ProcessIO(command_timeout=.1).run((sys.executable, '-c', script), env={}, log=io.StringIO())
    assert 'SECRET-ARGV' not in str(failure.value)


def test_secret_values_and_bearer_headers_are_redacted_across_chunks():
    stream = io.StringIO()
    script = "import sys; sys.stdout.write('x'*4090 + 'Authorization: Bearer TOKEN-VALUE\\n'); print('opaque ENV-SECRET')"
    ProcessIO().run((sys.executable, '-c', script), env={**os.environ, 'MY_SECRET': 'ENV-SECRET'}, log=stream)
    assert 'TOKEN-VALUE' not in stream.getvalue()
    assert 'ENV-SECRET' not in stream.getvalue()


def test_persisted_diagnostics_are_schema_filtered_and_symlink_confined(tmp_path):
    from h3server.installer.diagnostics import Diagnostics, read_diagnostics
    paths, plan, store = prepared(tmp_path)
    setup = store.create_setup(plan)
    log = Diagnostics(paths, setup.setup_id, setup.plan_digest)
    log.write('ordinary output\n')
    path = paths.logs / f'installer-{setup.setup_id}.diagnostics.json'
    record = json.loads(path.read_text())
    record['arbitrary_secret'] = 'NEVER-FORWARD'
    record['stage'] = '<script>'
    path.write_text(json.dumps(record))
    public = read_diagnostics(paths, setup)
    assert 'NEVER-FORWARD' not in json.dumps(public)
    assert '<script>' not in json.dumps(public)
    saved = paths.logs / 'saved.json'
    path.rename(saved)
    path.symlink_to(saved)
    with pytest.raises((OSError, ValueError)):
        read_diagnostics(paths, setup)
