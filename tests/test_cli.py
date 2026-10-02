"""Real launcher boundary tests; no SSH, downloads, or model runtime."""
import hashlib
import socket
from pathlib import Path
from unittest.mock import Mock

import pytest


def test_help_and_version(capsys):
    from h3server.__main__ import main
    with pytest.raises(SystemExit) as exc:
        main(['--help'])
    assert exc.value.code == 0
    assert 'deploy' in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        main(['--version'])
    assert exc.value.code == 0
    assert '0.1.0' in capsys.readouterr().out


def test_invalid_arguments_do_not_echo_secrets(capsys):
    from h3server.__main__ import main
    with pytest.raises(SystemExit) as exc:
        main(['--password', 'secret-never-print'])
    assert 'secret-never-print' not in str(exc.value) + str(capsys.readouterr())


def test_external_release_requires_both_pin_and_wheel(tmp_path):
    from h3server.__main__ import main
    with pytest.raises(SystemExit, match='together'):
        main(['deploy', '--wheel', str(tmp_path / 'x.whl')])


@pytest.mark.parametrize('browser', [True, False])
@pytest.mark.parametrize('interrupt', [True, False])
@pytest.mark.parametrize('browser_result', [True, False, None])
def test_launcher_reserves_port_real_app_release_and_cleanup(tmp_path, monkeypatch, capsys, browser, interrupt, browser_result):
    from h3server import __main__ as cli
    from test_deploy import wheel
    path = wheel(tmp_path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    opened = Mock(return_value=browser_result)
    if browser_result is None:
        opened.side_effect = cli.webbrowser.Error('browser unavailable')
    monkeypatch.setattr(cli.webbrowser, 'open', opened)
    observed = {}
    class Server:
        def __init__(self, config):
            observed['app'] = config.app
            assert config.access_log is False
        def run(self, sockets):
            listener, = sockets
            observed['listener'] = listener
            self.launch()
            assert listener.getsockname()[0] == '127.0.0.1'
            with socket.socket() as other, pytest.raises(OSError):
                other.bind(listener.getsockname())
            assert observed['app'].state.deployment.release.artifact.sha256 == digest
            if interrupt:
                raise KeyboardInterrupt()
    monkeypatch.setattr(cli, 'BrowserServer', Server)
    args = ['deploy', '--wheel', str(path), '--sha256', digest, '--state-dir', str(tmp_path)]
    if not browser:
        args.append('--no-browser')
    assert cli.main(args) == 0
    assert observed['listener'].fileno() == -1
    url = opened.call_args.args[0] if browser and browser_result else capsys.readouterr().out.strip()
    assert url.startswith('http://127.0.0.1:') and '/#token=' in url
    assert opened.call_count == int(browser)


def test_unavailable_port_and_invalid_release(tmp_path):
    from h3server.__main__ import main
    from test_deploy import wheel
    path = wheel(tmp_path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(SystemExit, match='verification failed'):
        main(['--wheel', str(path), '--sha256', '0'*64])
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        with pytest.raises(SystemExit, match='loopback port'):
            main(['--wheel', str(path), '--sha256', digest, '--port', str(listener.getsockname()[1])])


def test_source_without_payload_is_actionable(monkeypatch):
    from h3server import __main__ as cli
    monkeypatch.setattr(cli, 'files', lambda _: Path('/nonexistent-h3-resources'))
    with pytest.raises(SystemExit, match='build.*wheel'):
        cli.main(['--no-browser'])


def test_legacy_deploy_dispatch(monkeypatch):
    from h3server import __main__ as cli
    from h3server.controller import deploy
    call = Mock(return_value=0)
    monkeypatch.setattr(cli, 'main', call)
    assert deploy.main(['deploy', '--no-browser']) == 0
    call.assert_called_once_with(['deploy', '--no-browser'])


def test_access_survives_launches_until_explicit_reset(tmp_path, monkeypatch, capsys):
    from h3server import __main__ as cli
    from test_deploy import wheel
    path = wheel(tmp_path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    keys = []
    class Server:
        def __init__(self, config):
            keys.append(config.app.state.session.access_key)
        def run(self, sockets):
            self.launch()
    monkeypatch.setattr(cli, 'BrowserServer', Server)
    args = ['--no-browser', '--wheel', str(path), '--sha256', digest, '--state-dir', str(tmp_path / 'state')]
    assert cli.main(args) == 0
    assert cli.main(args) == 0
    assert keys[0] and keys[0] == keys[1]
    assert cli.main(args + ['--reset-access-key']) == 0
    assert keys[2] != keys[1]
    output = capsys.readouterr().out
    assert all(key not in output for key in keys)
