"""TEST ONLY target CLI: real authorization/activation/store, controlled native IO.

Only launched by generation_fixture.py against its temporary target directory.
No packages, services, network downloads or GPU operations are performed.
"""
import os
from pathlib import Path
import sys
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_deploy import RuntimeIO
from h3server.controller.target import main
from h3server.domain import Event, StageStatus
from h3server.downloads import DownloadCancelled
from h3server.installer.recovery import Recovery
from h3server.backends.base import atomic_json


def wait_for_gate(path):
    deadline = time.monotonic() + 30
    while not path.exists():
        assert time.monotonic() < deadline, 'TEST ONLY retry gate timed out'
        time.sleep(.01)


class ControlledRetry(Recovery):
    def __init__(self, paths, store):
        from types import SimpleNamespace
        from generation_fixture import PackageIO
        io = PackageIO(SimpleNamespace(calls=[]), paths)
        io.success = (paths.root/'fixture-package-success').exists()
        super().__init__(paths, store, io=io)

    def retry_stage(self, *args, **kwargs):
        result = super().retry_stage(*args, **kwargs)
        if (self.paths.root/'fixture-retry-gated').exists():
            (self.paths.root/'fixture-retry-ready').touch()
            wait_for_gate(self.paths.root/'fixture-retry-finish')
            if (self.paths.root/'fixture-retry-interrupt').exists():
                os._exit(1)  # Running record remains; the OS releases the actual flock.
        return result


def gated_record(paths, name, record):
    atomic_json(paths, name, record)
    if (name == 'state/operation.json' and record.get('action') == 'retry-stage'
            and record.get('state') == 'completed' and (paths.root/'fixture-retry-gated').exists()):
        (paths.root/'fixture-retry-recorded').touch()
        wait_for_gate(paths.root/'fixture-retry-release')


def download(paths, *, emit, cancelled, profile_name):
    from h3server.downloads import require_consent
    require_consent(paths, profile_name=profile_name)
    emit(Event('download', StageStatus.RUNNING, 'TEST ONLY controlled bytes, no model download.'))
    gate = paths.root / 'fixture-download-finish'
    deadline = time.monotonic() + 60
    while os.environ.get('H3_TEST_CONTROLLED_SETUP') == 'download' and not gate.exists():
        if cancelled():
            raise DownloadCancelled()
        assert time.monotonic() < deadline, 'TEST ONLY download gate timed out'
        time.sleep(.05)
    time.sleep(.8)
    if cancelled():
        raise DownloadCancelled()


if __name__ == '__main__':
    # main() still validates staged exact approval/consent and writes real durable
    # operation records. This subprocess replaces only detachment/native IO.
    with patch('h3server.controller.target._detach', return_value=True), \
         patch('h3server.controller.target.TargetIO', RuntimeIO), \
         patch('h3server.controller.target.download_model', download), \
         patch('h3server.controller.target.Recovery', ControlledRetry), \
         patch('h3server.controller.target.atomic_json', gated_record), \
         patch('h3server.backends.comfy_runtime.verify_sources'):
        main()
