"""Minimal .env-style file loader.

Not python-dotenv — this project keeps its dependency list to what's actually needed (see
llm.py's urllib instead of an SDK, parser.py's stdlib-only HTTP calls), and a `KEY=value` reader
is a dozen lines. Exists so local secrets can live in a gitignored file instead of needing
`$env:...` set by hand in every new terminal session.

Real environment variables always win over the file — this only fills in values nothing else
already set, matching the common .env-loader convention.
"""
from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: str | Path = '.env.local') -> None:
    file = Path(path)
    if not file.exists():
        return
    for raw_line in file.read_text(encoding='utf-8').splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, _, value = line.partition('=')
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in '"\'':
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)
