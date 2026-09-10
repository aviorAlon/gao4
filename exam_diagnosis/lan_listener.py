"""An extra uvicorn listener on 0.0.0.0, opened only while a phone-upload session is live.

The alternative would have been to bind the main server to all interfaces and rely on
authorisation alone. This is better: for the overwhelming majority of the time the app is running,
there is simply no socket for anything on the network to talk to. The window is the few minutes
someone is photographing a paper.

Starting and stopping is idempotent and reference-counted by the caller (app.py closes the
listener when the last session ends). Failures to bind are reported, not raised: not being able
to open a LAN port is a "phone upload is unavailable" message, not a reason to break the app.
"""
from __future__ import annotations

import socket
import threading
from typing import Any


class LanListener:
    """Owns at most one background uvicorn serving `app` on 0.0.0.0."""

    def __init__(self, app: Any, host: str = '0.0.0.0', log_level: str = 'warning'):
        self.app = app
        self.host = host
        self.log_level = log_level
        self._lock = threading.Lock()
        self._server = None
        self._thread: threading.Thread | None = None
        self.port: int | None = None
        self.error: str | None = None

    @property
    def running(self) -> bool:
        return self.port is not None

    @staticmethod
    def _free_port(host: str = '0.0.0.0') -> int:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((host, 0))
            return probe.getsockname()[1]

    def start(self, timeout: float = 10.0) -> int | None:
        """Bind and serve; returns the port, or None with `error` set. Already-running is a no-op."""
        with self._lock:
            if self.port is not None:
                return self.port
            import uvicorn

            self.error = None
            try:
                port = self._free_port(self.host)
            except OSError as exc:
                self.error = f'无法在本机网络接口上开端口：{exc}'
                return None
            ready = threading.Event()

            class ReadyServer(uvicorn.Server):
                async def startup(self, sockets=None):
                    await super().startup(sockets=sockets)
                    ready.set()

                def install_signal_handlers(self):
                    # This runs on a worker thread, where signal handlers can't be installed —
                    # and the process's real handlers belong to the main server anyway.
                    pass

            server = ReadyServer(uvicorn.Config(self.app, host=self.host, port=port, log_level=self.log_level))
            thread = threading.Thread(target=server.run, name='exam-diagnosis-lan', daemon=True)
            thread.start()
            if not ready.wait(timeout):
                server.should_exit = True
                thread.join(timeout=5)
                self.error = '局域网监听启动超时'
                return None
            self._server, self._thread, self.port = server, thread, port
            return port

    def stop(self, timeout: float = 5.0) -> None:
        with self._lock:
            if self._server is None:
                self.port = None
                return
            self._server.should_exit = True
            if self._thread is not None:
                self._thread.join(timeout=timeout)
            self._server, self._thread, self.port = None, None, None

    def describe(self) -> dict[str, Any]:
        return {'running': self.running, 'host': self.host, 'port': self.port, 'error': self.error}
