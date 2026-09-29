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
         patch('h3server.controller.target.download_model', download):
        main()
