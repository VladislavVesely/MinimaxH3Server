"""Source/remote-wheel equality, without building or importing inference libraries."""
import importlib.util
from pathlib import Path
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


def build_module():
    spec = importlib.util.spec_from_file_location('build_controller', ROOT / 'scripts/build_controller.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('corrupt', [False, True])
def test_wheel_verifier_checks_pruned_runtime_and_source_equality(tmp_path, corrupt):
    wheel = tmp_path / 'h3server-0.1.0-py3-none-any.whl'
    with zipfile.ZipFile(wheel, 'w') as archive:
        for path in (ROOT / 'src/h3server').rglob('*'):
            if path.is_file() and path.suffix in {'.py', '.json', '.txt', '.js', '.css', '.html'}:
                data = path.read_bytes()
                if corrupt and path.name == 'catalog.py':
                    data = b'# stale legacy distribution\n'
                archive.writestr(path.relative_to(ROOT / 'src').as_posix(), data)
    if corrupt:
        with pytest.raises(AssertionError, match='catalog.py'):
            build_module().verify_wheel(wheel)
    else:
        assert len(build_module().verify_wheel(wheel)) == 64
