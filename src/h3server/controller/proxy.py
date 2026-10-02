"""Authenticated HTTP over SSH direct-tcpip; no forwarding listener or browser token."""
from http.client import HTTPConnection
import json
import re

from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from h3server.controller.deploy import _read
from h3server.controller.ssh import ControllerError, _TransportDeadline


class _ChannelSocket:
    """Keep Paramiko's channel alive until HTTPResponse closes its file.

    HTTPConnection relinquishes a Connection:close socket after parsing headers.
    Real socket.makefile keeps a descriptor reference; Channel.makefile does not.
    This adapter gives it the same ownership semantics for streaming responses.
    """
    def __init__(self, channel):
        self.channel, self.owner, self.files = channel, True, 0

    def __getattr__(self, name):
        return getattr(self.channel, name)

    def makefile(self, mode):
        parent = self
        stream = self.channel.makefile(mode)
        self.files += 1
        class ResponseFile:
            closed = False
            def __getattr__(self, name):
                if stream is None:
                    raise ValueError('I/O operation on closed response')
                return getattr(stream, name)
            def close(self):
                nonlocal stream
                if not self.closed:
                    self.closed = True
                    owned, stream = stream, None
                    try:
                        owned.close()
                    finally:
                        parent.files -= 1
                        parent._close_if_unused()
        return ResponseFile()

    def _close_if_unused(self):
        if not self.owner and not self.files:
            self.channel.close()

    def close(self):
        self.owner = False
        self._close_if_unused()


class RemoteProxy:
    def __init__(self, connection):
        self.connection = connection

    def request_json(self, method, path, *, body=None):
        """Bounded internal JSON use by the setup worker, never a browser token."""
        import asyncio
        response = self.request(method, path, body=(body if body is not None else {}) if method == 'POST' else None)
        async def collect():
            data = bytearray()
            try:
                async for chunk in response.body_iterator:
                    data.extend(chunk)
                    if len(data) > 65536:
                        raise ControllerError('invalid_remote_response')
            finally:
                if response.background is not None:
                    await response.background()
            return bytes(data)
        data = asyncio.run(collect())
        if response.status_code >= 400:
            detail = json.loads(data).get('detail')
            if path == '/api/v1/settings/memory':
                safe = {'memory_settings_unavailable','memory_settings_busy','memory_settings_changed','memory_settings_unsupported'}
                raise ControllerError(detail if detail in safe else 'memory_settings_unavailable', response.status_code)
            if detail in ('turbo_selection_changed', 'sparse_selection_changed'):
                raise ControllerError(detail)
            raise ControllerError('optional_install_busy_or_unsupported')
        return json.loads(data)

    def request(self, method, path, *, body=None, range_header=None, binary=None):
        if not (method == 'GET' and re.fullmatch(r'/api/v1/(?:characters(?:/[a-f0-9]{32})?|assets/[a-f0-9]{64}(?:/content)?|health|capabilities|settings/memory|models(?:/library)?|performance(?:\?offset=[0-9]{1,10})?|features/(?:sageattention|turbo|sparse|reference)|jobs(?:\?limit=1000)?|events\?after=[0-9]{1,19}&follow=false|jobs/[a-f0-9]{32}(?:/(?:video(?:\?download=true)?|last-frame|audio))?)', path)
                or method == 'POST' and re.fullmatch(r'/api/v1/(?:characters(?:/[a-f0-9]{32}/(?:update|delete))?|assets/(?:image|video|audio)|settings/memory|models/(?:unload|install(?:/cancel)?)|performance/estimate|features/(?:sageattention|turbo|sparse|reference)|jobs(?:/[a-f0-9]{32}/(?:cancel|delete|favorite))?)', path)):
            raise ControllerError('proxy_route_forbidden', 404)
        if binary is not None:
            from h3server.assets import MAX_BYTES
            if method != 'POST' or not re.fullmatch(r'/api/v1/assets/(?:image|video|audio)',path) or body is not None or not isinstance(binary,bytes) or not 0<len(binary)<=MAX_BYTES:
                raise ControllerError('invalid_asset_upload',422)
        if range_header is not None and not re.fullmatch(r'bytes=(?:[0-9]{1,20}-[0-9]{0,20}|-[0-9]{1,20})', range_header):
            raise ControllerError('invalid_range', 416)
        channel = None
        deadline = None
        try:
            with self.connection.lock:
                self.connection._require_authentication()
                root = self.connection.remote_home().rstrip('/') + '/MinimaxH3Server'
                with self.connection.private_sftp() as sftp:
                    token = _read(sftp, root + '/config/remote-token', 257).decode('ascii').strip()
                if not re.fullmatch(r'[A-Za-z0-9_-]{32,256}', token):
                    raise ValueError('invalid token')
                channel = self.connection.open_api_channel()
                deadline = _TransportDeadline(self.connection._socket, self.connection._transport, 300)
                deadline.__enter__()
            http = HTTPConnection('127.0.0.1', 8765, timeout=30)
            http.sock = _ChannelSocket(channel)
            headers = {'Authorization': 'Bearer ' + token, 'Connection': 'close'}
            if range_header:
                headers['Range'] = range_header
            data = None if body is None else json.dumps(body).encode()
            if data is not None:
                headers['Content-Type'] = 'application/json'
            if binary is not None:
                data=binary;headers['Content-Type']='application/octet-stream'
            http.request(method, path, body=data, headers=headers)
            response = http.getresponse()
            # No Location, Set-Cookie, auth challenge or arbitrary remote headers.
            outgoing = {k: v for k, v in response.getheaders() if k.lower() in
                        {'content-type', 'content-length', 'content-range', 'accept-ranges', 'content-disposition', 'retry-after', 'cache-control', 'x-content-type-options'}}
            closed = False
            def close():
                nonlocal closed
                if closed:
                    return
                closed = True
                try:
                    response.close()
                    http.close()
                finally:
                    deadline.__exit__(None, None, None)
            def chunks():
                try:
                    while chunk := response.read(65536):
                        yield chunk
                finally:
                    close()
            return StreamingResponse(chunks(), status_code=response.status, headers=outgoing,
                                     background=BackgroundTask(close))
        except Exception as error:
            try:
                if channel is not None:
                    channel.close()
            finally:
                if deadline is not None:
                    deadline.__exit__(None, None, None)
            if isinstance(error, ControllerError):
                raise
            raise ControllerError('remote_unavailable', 502) from None
