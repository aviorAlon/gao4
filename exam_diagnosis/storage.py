"""Small SQLite-backed mapping used to keep the MVP state across restarts."""
from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import MutableMapping, Iterator
from pathlib import Path
from typing import Any

from . import paths


class SQLiteMapping(MutableMapping[str, dict[str, Any]]):
    def __init__(self, name: str, path: str | None = None):
        self.name = name
        self.path = Path(path).resolve() if path else paths.database_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, dict[str, Any]] = {}
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS state (bucket TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(bucket, key))")

    def _connect(self):
        return sqlite3.connect(self.path)

    def __getitem__(self, key: str) -> dict[str, Any]:
        if key in self._cache:
            return self._cache[key]
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM state WHERE bucket=? AND key=?", (self.name, key)).fetchone()
        if row is None:
            raise KeyError(key)
        self._cache[key] = json.loads(row[0])
        return self._cache[key]

    def __setitem__(self, key: str, value: dict[str, Any]) -> None:
        self._cache[key] = value
        payload = json.dumps(value, ensure_ascii=False)
        with self._connect() as conn:
            conn.execute("INSERT OR REPLACE INTO state(bucket,key,value) VALUES (?,?,?)", (self.name, key, payload))

    def __delitem__(self, key: str) -> None:
        self._cache.pop(key, None)
        with self._connect() as conn:
            cursor = conn.execute("DELETE FROM state WHERE bucket=? AND key=?", (self.name, key))
            if cursor.rowcount == 0:
                raise KeyError(key)

    def __iter__(self) -> Iterator[str]:
        with self._connect() as conn:
            rows = conn.execute("SELECT key FROM state WHERE bucket=?", (self.name,)).fetchall()
        return iter(set(row[0] for row in rows) | set(self._cache))

    def __len__(self) -> int:
        with self._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM state WHERE bucket=?", (self.name,)).fetchone()[0]

    def clear(self) -> None:
        self._cache.clear()
        with self._connect() as conn:
            conn.execute("DELETE FROM state WHERE bucket=?", (self.name,))

    def flush(self) -> None:
        with self._connect() as conn:
            for key, value in self._cache.items():
                conn.execute("INSERT OR REPLACE INTO state(bucket,key,value) VALUES (?,?,?)", (self.name, key, json.dumps(value, ensure_ascii=False)))

    def repoint(self, path: str | Path) -> None:
        """Redirect this mapping at a different sqlite file, dropping any in-memory cache from the
        old one. Exists so tests can point every mapping at an isolated file instead of `clear()`
        being the only reset available — `clear()` alone, with the default relative path, means
        test runs and a locally running dev server both resolve to the same file when launched
        from the same working directory, and a test suite wiping "its own" state wipes real data."""
        self.path = Path(path).resolve()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._cache.clear()
        with self._connect() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS state (bucket TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL, PRIMARY KEY(bucket, key))")
