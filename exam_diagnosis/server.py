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
    return parser.parse_args(argv)


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
    port = args.port or pick_free_port(args.host)
    server = build_server(args.host, port, args.log_level)
    server.run()
    return 0


if __name__ == '__main__':
    sys.exit(main())
