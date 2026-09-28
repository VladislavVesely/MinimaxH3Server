"""TEST ONLY: real HTTP controller, controlled SSH socket/Paramiko boundary.

Run with .venv/bin/python tests/browser/controller_fixture.py --port 8123.
The stdout launch URL is a test session, never a real server connection.
"""
import argparse
import json
from pathlib import Path
import socket
import sys
import tempfile
import time

import paramiko
import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from test_ssh import FakeTransport, Network  # noqa: E402
from h3server.controller.app import create_app  # noqa: E402


class BrowserTransport(FakeTransport):
    def auth_password(self, username, password, fallback):
        if password == "wrong-password":
            raise paramiko.AuthenticationException("not logged or returned")
        super().auth_password(username, password, fallback)

    def open_session(self, timeout):
        # Visible indeterminate progress, without fabricating measured units.
        time.sleep(0.04)
        return super().open_session(timeout)


class BrowserNetwork(Network):
    def socket(self, address, timeout):
        if address[0] == "unreachable.example":
            raise OSError("controlled connection failure")
        if address[0] == "changed-key.example" and self.sockets:
            self.key = paramiko.RSAKey.generate(1024)
        self.outputs["printf '%s' \"$HOME\""] = b"" if address[0] == "scan-failure.example" else b"/home/test"
        return super().socket(address, timeout)

    def transport(self, sock):
        transport = BrowserTransport(self, sock)
        self.transports.append(transport)
        return transport


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()
    network = BrowserNetwork()
    network.outputs.update({
        "LC_ALL=C uname -s": b"Linux",
        "LC_ALL=C uname -m": b"x86_64",
        "LC_ALL=C cat /etc/os-release": b'ID=ubuntu\nVERSION_ID="24.04"\n',
        "LC_ALL=C cat /proc/cpuinfo": b'processor : 0\nmodel name : <img src=x onerror=alert(1)>\n',
        "LC_ALL=C cat /proc/meminfo": b"MemTotal: 134217728 kB\n",
        "LC_ALL=C cat /proc/1/comm": b"systemd\n",
    })
    with tempfile.TemporaryDirectory(prefix="h3-browser-fixture-") as state, socket.socket() as listener:
        listener.bind(("127.0.0.1", args.port))
        origin = f"http://127.0.0.1:{listener.getsockname()[1]}"
        app = create_app(state_dir=Path(state), origin=origin,
                         socket_factory=network.socket, transport_factory=network.transport)
        print(json.dumps({"launch": origin + "/#token=" + app.state.session.bootstrap_token,
                          "fixture": "TEST ONLY — controlled SSH transport, no real server"}), flush=True)
        uvicorn.Server(uvicorn.Config(app, log_level="warning", access_log=False)).run(sockets=[listener])


if __name__ == "__main__":
    main()
