"""Run every check CI runs, locally, with one command.

    python scripts/check.py            # backend tests + frontend lint + type-check/build
    python scripts/check.py --backend  # just the tests
    python scripts/check.py --frontend # just lint + build

Mirrors .github/workflows/ci.yml, so "it passed here" means the same thing as a green CI run —
useful now, before the repository has a remote, and after, for checking before pushing.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / 'frontend'


def npm() -> str:
    # On Windows npm is a .cmd shim, which subprocess won't find by the bare name.
    found = shutil.which('npm') or shutil.which('npm.cmd')
    if not found:
        raise SystemExit('npm not found on PATH — install Node.js to run the frontend checks')
    return found


def steps(backend: bool = True, frontend: bool = True) -> list[tuple[str, list[str], Path]]:
    planned: list[tuple[str, list[str], Path]] = []
    if backend:
        planned.append(('backend tests', [sys.executable, '-m', 'pytest', '-q'], ROOT))
    if frontend:
        planned.append(('frontend lint', [npm(), 'run', 'lint'], FRONTEND))
        planned.append(('frontend type-check + build', [npm(), 'run', 'build'], FRONTEND))
    return planned


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--backend', action='store_true')
    group.add_argument('--frontend', action='store_true')
    args = parser.parse_args(argv)
    planned = steps(backend=not args.frontend, frontend=not args.backend)
    results = []
    for name, command, cwd in planned:
        print(f'\n=== {name} ===', flush=True)
        started = time.monotonic()
        code = subprocess.call(command, cwd=cwd)
        results.append((name, code, time.monotonic() - started))
    print('\n=== summary ===')
    for name, code, seconds in results:
        print(f"{'PASS' if code == 0 else 'FAIL'}  {name}  ({seconds:.0f}s)")
    return 0 if all(code == 0 for _, code, _ in results) else 1


if __name__ == '__main__':
    sys.exit(main())
