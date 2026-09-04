"""Where this instance keeps its data.

One root — EXAM_DIAGNOSIS_DATA_DIR, default `data` under the current directory (exactly what the
web deployment has always used) — and everything writable lives under it: the SQLite file,
uploads, config.json. The desktop shell points this at the user's app-data folder; the web
deployment doesn't set it and nothing changes. Evaluated on every call rather than at import so
a process can be told where to live before the app module is imported (see server.py) and so
tests can redirect it per test.
"""
from __future__ import annotations

import os
from pathlib import Path

DEFAULT_DATA_DIR = 'data'


def data_dir() -> Path:
    return Path(os.getenv('EXAM_DIAGNOSIS_DATA_DIR') or DEFAULT_DATA_DIR).resolve()


def database_path() -> Path:
    configured = os.getenv('EXAM_DIAGNOSIS_DB')
    return Path(configured).resolve() if configured else data_dir() / 'exam_diagnosis.sqlite3'


def uploads_dir() -> Path:
    return data_dir() / 'uploads'


def config_path() -> Path:
    return data_dir() / 'config.json'
