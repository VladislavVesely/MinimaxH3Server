import importlib.util
from pathlib import Path
import sys
import pytest


def test_build_refuses_to_replace_an_existing_native_executable(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('build_controller',Path(__file__).parents[1]/'scripts/build_controller.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    target=tmp_path/('h3-controller.exe' if sys.platform=='win32' else 'h3-controller')
    target.write_bytes(b'possibly running archived controller')
    monkeypatch.setattr(sys,'argv',['build_controller.py','--distpath',str(tmp_path)])
    def unexpected(*a,**kw):pytest.fail('Build started before checking retained executable')
    monkeypatch.setattr(module.subprocess,'run',unexpected)
    with pytest.raises(SystemExit,match='already exists'):module.main()
    assert target.read_bytes()==b'possibly running archived controller'
