"""Bounded, setup-owned diagnostics. Never accepts a caller-supplied log path.

This is a tail, not an audit archive: offsets count sanitized UTF-8 bytes and a
consumer replaces its view when start_offset advances. No estimated progress.
"""
import re
import time
import unicodedata

from h3server.backends.base import atomic_json, read_json, regular_file

TAIL_BYTES = 32 * 1024


def safe_log(text):
    text = re.sub(r'\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)', '', text)
    text = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', text)
    text = re.sub(r'(https?://)[^/\s@]+@', r'\1[redacted]@', text)
    text = re.sub(r'(https?://[^\s?#]+)[?#][^\s]*', r'\1[redacted]', text)
    text = re.sub(r'(?i)\b(authorization|password|passwd|token|secret|api[_-]?key)(\s*[:=]\s*)[^\s]+', r'\1\2[redacted]', text)
    # Keep plain text safe even for accidental HTML rendering. ESC/format/bidi
    # controls and tags are never meaningful installer output.
    return ''.join(c for c in text if c not in '<>' and (c in '\n\t' or unicodedata.category(c) not in {'Cc', 'Cf'}))


def bounded(text, limit=TAIL_BYTES):
    return text.encode('utf-8')[-limit:].decode('utf-8', 'ignore')


def retry_reason(text):
    text = text.lower()
    if any(s in text for s in ('resolutionimpossible', 'conflicting dependencies', 'permission denied', 'no space left', 'requires a different python')):
        return None
    if 'no matching distribution found' in text or 'could not find a version that satisfies' in text:
        return 'fresh-index'
    if any(s in text for s in ('readtimeouterror', 'connection timed out', 'temporary failure in name resolution',
                              'connection reset', 'connection aborted', 'remote end hung up', 'http 503', 'http 502',
                              '502 bad gateway', '503 service unavailable', 'could not resolve host')):
        return 'transient-download'
    return None


def _name(setup_id):
    if not re.fullmatch('[a-f0-9]{32}', setup_id):
        raise ValueError('invalid setup identifier')
    return f'logs/installer-{setup_id}.diagnostics.json'


class Diagnostics:
    def __init__(self, paths, setup_id, digest):
        self.paths, self.name = paths, _name(setup_id)
        self.record = {'schema': 1, 'setup_id': setup_id, 'plan_digest': digest,
                       'stage': None, 'substage': None, 'command': None, 'tail': '',
                       'offset': 0, 'start_offset': 0, 'retry': None, 'failure': None}
        self._last_flush = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.flush()

    def phase(self, stage, substage, label):
        self.record.update(stage=stage, substage=substage)
        self.label = safe_log(label)

    def started(self, fallback):
        now = time.time()
        self.record['command'] = {'label': getattr(self, 'label', fallback), 'started_at': now,
                                  'ended_at': None, 'exit_code': None, 'last_output_at': None, 'error': None}
        self.record['failure'] = None
        self.flush()

    def ended(self, code, error=None):
        if self.record['command']:
            self.record['command'].update(ended_at=time.time(), exit_code=code, error=safe_log(error) if error else None)
        self.flush()

    def failed(self, error):
        self.record['failure'] = bounded(safe_log(str(error)), 4096)
        self.flush()

    def retry(self, attempt, reason, delay):
        self.record['retry'] = {'attempt': attempt, 'reason': reason, 'delay_seconds': delay, 'at': time.time()}
        self.write(f'\nRetry {attempt}: {reason}; next attempt in {delay}s; exact pins unchanged.\n')
        self.flush()

    def write(self, text):
        text = safe_log(text)
        self.record['offset'] += len(text.encode())
        self.record['tail'] = bounded(self.record['tail'] + text)
        self.record['start_offset'] = self.record['offset'] - len(self.record['tail'].encode())
        if self.record['command']:
            self.record['command']['last_output_at'] = time.time()
        if time.monotonic() - self._last_flush >= .25:
            self.flush()

    def flush(self):
        atomic_json(self.paths, self.name, self.record)
        self._last_flush = time.monotonic()


def read_diagnostics(paths, setup):
    name = _name(setup.setup_id)
    try:
        record = read_json(paths, name, limit=150000)
        if record['setup_id'] != setup.setup_id or record['plan_digest'] != setup.plan_digest:
            raise ValueError('diagnostics binding mismatch')
        # Re-sanitize even persisted data at the boundary.
        record['tail'] = bounded(safe_log(record['tail']))
        if record.get('failure'):
            record['failure'] = bounded(safe_log(record['failure']), 4096)
        if record.get('command'):
            for field in ('label', 'error'):
                if record['command'].get(field):
                    record['command'][field] = bounded(safe_log(record['command'][field]), 4096)
        return record
    except FileNotFoundError:
        try:
            with regular_file(paths, f'logs/installer-{setup.setup_id}.log') as stream:
                stream.seek(0, 2)
                size = stream.tell()
                stream.seek(max(0, size - TAIL_BYTES))
                tail = bounded(safe_log(stream.read(TAIL_BYTES).decode('utf-8', 'replace')))
        except FileNotFoundError:
            tail = ''
        return {'schema': 1, 'setup_id': setup.setup_id, 'plan_digest': setup.plan_digest,
                'stage': next((s.name for s in setup.stages if s.status.value == 'failed'), None),
                'substage': None, 'command': None, 'tail': tail, 'offset': len(tail.encode()),
                'start_offset': 0, 'retry': None, 'failure': None, 'legacy': True}
