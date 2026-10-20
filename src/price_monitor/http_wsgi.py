"""WSGI transport; Waitress owns HTTP parsing, timeouts and worker management."""
from __future__ import annotations

from email.message import Message
from http import HTTPStatus
from types import SimpleNamespace
import threading

from waitress.server import create_server
from waitress import wasyncore

from .config import TRUSTED_PROXY, URL_SCHEME


class WSGIRequest:
    def __init__(self, environ):
        self.environ = environ
        self.command = environ["REQUEST_METHOD"].upper()
        self.path = environ.get("PATH_INFO", "/") + ("?" + environ["QUERY_STRING"] if environ.get("QUERY_STRING") else "")
        self.headers = Message()
        for name, value in environ.items():
            if name.startswith("HTTP_"):
                self.headers[name[5:].replace("_", "-")] = value
        for name in ("CONTENT_TYPE", "CONTENT_LENGTH"):
            if environ.get(name):
                self.headers[name.replace("_", "-")] = environ[name]
        self.rfile = environ["wsgi.input"]
        self.client_address = (environ.get("REMOTE_ADDR", ""), 0)
        self.server = SimpleNamespace(server_port=int(environ.get("SERVER_PORT", "80")))
        self._response_status = 500
        self.response_body = b""
        self.response_headers = []
        self.principal = None

    def send_response(self, code, message=None):
        self._response_status = int(code)

    def send_header(self, name, value):
        self.response_headers.append((name, str(value)))

    def end_headers(self):
        pass


def wsgi_application(environ, start_response, handler):
    request = handler(environ)
    request.handle_request()
    code = request._response_status
    start_response(f"{code} {HTTPStatus(code).phrase}", request.response_headers)
    return [] if request.command == "HEAD" else [request.response_body]


class WSGIServer:
    """Small lifecycle wrapper also used by real HTTP integration tests."""
    def __init__(self, address, handler):
        options = dict(host=address[0], port=address[1], threads=8, max_request_body_size=8 * 1024 * 1024,
                       channel_timeout=60, asyncore_loop_timeout=0.1, clear_untrusted_proxy_headers=True,
                       url_scheme=URL_SCHEME)
        if TRUSTED_PROXY:
            options.update(trusted_proxy=TRUSTED_PROXY,
                           trusted_proxy_headers={"x-forwarded-for", "x-forwarded-proto", "x-forwarded-host", "x-forwarded-port"})
        self._map = {}
        self._stop = threading.Event()
        self._running = threading.Event()
        self._closed = threading.Event()
        self._server = create_server(lambda env, start: wsgi_application(env, start, handler), map=self._map, **options)
        self.server_port = int(self._server.effective_port)
        self.server_address = (address[0], self.server_port)

    def serve_forever(self):
        self._running.set()
        try:
            while not self._stop.is_set():
                wasyncore.loop(timeout=0.1, count=1, map=self._map)
        finally:
            self._server.task_dispatcher.shutdown(timeout=2)
            wasyncore.close_all(map=self._map)
            self._closed.set()

    def shutdown(self):
        self._stop.set()
        if self._running.is_set():
            self._closed.wait(3)
        elif not self._closed.is_set():
            self._server.task_dispatcher.shutdown(timeout=2)
            wasyncore.close_all(map=self._map)
            self._closed.set()

    def server_close(self):
        self.shutdown()
