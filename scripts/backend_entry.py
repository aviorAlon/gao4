"""PyInstaller entry point for the bundled backend — the frozen executable runs exactly what
`python -m exam_diagnosis.server` runs, so the desktop shell and a developer's terminal drive
the same code path."""
import multiprocessing
import sys

from exam_diagnosis.server import main

if __name__ == '__main__':
    # Frozen Windows builds re-import the entry module in child processes; uvicorn doesn't
    # spawn any, but this keeps a stray spawn from becoming a second server.
    multiprocessing.freeze_support()
    sys.exit(main())
