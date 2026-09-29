"""Guard checks only; never authorizes or executes a target mutation."""
import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def acceptance():
    spec = importlib.util.spec_from_file_location('target_acceptance', Path(__file__).resolve().parents[1] / 'scripts/target_acceptance.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_acceptance_help_has_no_target_imports(acceptance, capsys):
    with pytest.raises(SystemExit) as exc:
        acceptance.main(['--help'])
    assert exc.value.code == 0
    assert 'verify-reboot' in capsys.readouterr().out


def test_no_nonlinux_target_execution(acceptance, monkeypatch):
    monkeypatch.setattr(acceptance.sys, 'platform', 'darwin')
    with pytest.raises(SystemExit, match='Linux target'):
        acceptance.main(['restart'])


@pytest.mark.parametrize('action', ['restart', 'generation', 'rollback', 'reboot'])
def test_no_blanket_or_empty_consent(acceptance, monkeypatch, action):
    monkeypatch.setattr('builtins.input', lambda _: 'yes')
    with pytest.raises(SystemExit, match='not approved'):
        acceptance.confirmation(action, disposable=True)


def test_reboot_needs_disposable_vm_flag_before_prompt(acceptance, monkeypatch):
    monkeypatch.setattr('builtins.input', lambda _: pytest.fail('must refuse before prompt'))
    with pytest.raises(SystemExit, match='--disposable-vm'):
        acceptance.confirmation('reboot')


def test_reboot_cannot_reuse_general_confirmation(acceptance, monkeypatch):
    monkeypatch.setattr(acceptance.socket, 'gethostname', lambda: 'test-host')
    monkeypatch.setattr('builtins.input', lambda _: 'ACCEPT REBOOT test-host')
    with pytest.raises(SystemExit, match='not approved'):
        acceptance.confirmation('reboot', disposable=True)
