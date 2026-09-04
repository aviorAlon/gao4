"""Command-line entry point for running the API server as a managed child process.

`python -m exam_diagnosis.server [--host H] [--port P] [--data-dir D]` — the same app uvicorn
serves for the web deployment, plus what a supervising desktop shell needs: `--port 0` picks a
free port, `--data-dir` says where to keep state before the app module is imported, and once the
socket is bound one line `LISTENING <host>:<port>` goes to stdout so the parent knows where to
connect. The web deployment keeps using `uvicorn exam_diagnosis.app:app` as before.
"""
from __future__ import annotations

import argparse
import os
import socket
import sys
import threading
import time
from typing import IO


def pick_free_port(host: str = '127.0.0.1') -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((host, 0))
        return probe.getsockname()[1]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog='exam_diagnosis.server', description='Run the Exam Diagnosis API server.')
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8765, help='0 = pick a free port (announced on stdout as LISTENING host:port)')
    parser.add_argument('--data-dir', default=None, help='where to keep the database, uploads and config.json (EXAM_DIAGNOSIS_DATA_DIR)')
    parser.add_argument('--log-level', default='info')
    parser.add_argument('--parent-pid', type=int, default=None, help='exit as soon as this process is gone (the desktop shell passes its own pid)')
    return parser.parse_args(argv)


def watch_parent(pid: int, on_exit=None, poll_seconds: float = 1.0) -> threading.Thread:
    """Exit when the supervising process disappears.

    The shell kills the backend on a clean exit, but a crash, a Task Manager kill or a forced
    close never runs that code — and an orphaned server would sit on a port with the user's
    data open until reboot. On Windows the parent's process handle becomes signalled when it
    exits (no polling); elsewhere the pid is probed with signal 0. Daemon thread, so it never
    keeps the process alive itself."""
    exit_now = on_exit or (lambda: os._exit(0))

    def run():
        if sys.platform == 'win32':
            import ctypes
            from ctypes import wintypes

            kernel32 = ctypes.windll.kernel32
            SYNCHRONIZE = 0x00100000
            handle = kernel32.OpenProcess(SYNCHRONIZE, False, wintypes.DWORD(pid))
            if handle:
                kernel32.WaitForSingleObject(handle, 0xFFFFFFFF)  # INFINITE
                kernel32.CloseHandle(handle)
                exit_now()
                return
            # No handle (already gone, or not permitted): fall through to polling.
        while True:
            try:
                os.kill(pid, 0)
            except OSError:
                exit_now()
                return
            time.sleep(poll_seconds)

    thread = threading.Thread(target=run, name='parent-watchdog', daemon=True)
    thread.start()
    return thread


def build_server(host: str, port: int, log_level: str = 'info', announce: IO[str] | None = None):
    """A uvicorn Server that reports its bound address once startup is done. Importing the app
    happens here, after the data dir is settled, because app.py reads it at import."""
    import uvicorn

    from .app import app

    out = announce if announce is not None else sys.stdout

    class AnnouncingServer(uvicorn.Server):
        async def startup(self, sockets=None):
            await super().startup(sockets=sockets)
            print(f'LISTENING {host}:{port}', file=out, flush=True)

    return AnnouncingServer(uvicorn.Config(app, host=host, port=port, log_level=log_level))


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.data_dir:
        os.environ['EXAM_DIAGNOSIS_DATA_DIR'] = args.data_dir
    if args.parent_pid:
        watch_parent(args.parent_pid)
    port = args.port or pick_free_port(args.host)
    server = build_server(args.host, port, args.log_level)
    server.run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
