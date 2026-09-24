"""Throwaway backend for verifying the frontend against the current code without touching the
developer's own running server or database: isolated SQLite in a temp dir, no model credentials,
auth optional. Usage: python exam_diagnosis/scripts/verify_server.py [port]"""
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

state_dir = Path(tempfile.mkdtemp(prefix='exam_diagnosis_verify_'))
# Everything writable — database, uploads, config.json from the settings page — stays in the
# temp dir; nothing from a verification session may land in the repo's data/.
os.environ['EXAM_DIAGNOSIS_DATA_DIR'] = str(state_dir)
# app.py loads .env.local from the cwd via setdefault — a real key sitting there must never make
# this throwaway server spend money. An empty value counts as "unset" for client_from_env and
# blocks setdefault from filling it in.
for role in ('ANALYSIS', 'REPORT', 'SPLIT', 'VERIFY', 'GRADE', 'MATCH', 'VISION'):
    os.environ[f'EXAM_DIAGNOSIS_{role}_API_KEY'] = ''
os.environ['EXAM_DIAGNOSIS_AUTH_REQUIRED'] = '0'

import uvicorn  # noqa: E402

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8767
print(f'verify server: state in {state_dir}, port {port}')
uvicorn.run('exam_diagnosis.app:app', host='127.0.0.1', port=port)
