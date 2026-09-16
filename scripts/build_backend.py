"""Freeze the API backend with PyInstaller for the desktop build, then prove the result works.

    python scripts/build_backend.py            # build + smoke test
    python scripts/build_backend.py --no-smoke # build only
    python scripts/build_backend.py --smoke-only

Output: frontend/src-tauri/resources/backend/<exe name>[.exe] plus its _internal/ folder — a
onedir bundle (starts faster than onefile and is far less often flagged by antivirus, since it
doesn't unpack an executable into %TEMP% on every launch). The Tauri shell ships that folder as
a resource and spawns the executable inside it.

The smoke test is not optional politeness: PyInstaller silently drops data files and lazy
imports it didn't see, and the two libraries most likely to break that way (PyMuPDF's native
library, python-docx's bundled templates) are exercised by generating a real report and
exporting it as PDF and Word through the frozen binary.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / 'scripts' / 'backend_entry.py'
EXE_NAME = 'exam-diagnosis-backend'
OUTPUT_DIR = ROOT / 'frontend' / 'src-tauri' / 'resources' / 'backend'
WORK_DIR = ROOT / 'build' / 'pyinstaller'

# Modules uvicorn/starlette import by string at runtime — PyInstaller's static analysis can't
# see them, and a missing one only shows up as a crash on first request.
HIDDEN_IMPORTS = [
    'uvicorn.logging', 'uvicorn.loops', 'uvicorn.loops.auto', 'uvicorn.loops.asyncio',
    'uvicorn.protocols', 'uvicorn.protocols.http', 'uvicorn.protocols.http.auto', 'uvicorn.protocols.http.h11_impl',
    'uvicorn.protocols.websockets', 'uvicorn.protocols.websockets.auto',
    'uvicorn.lifespan', 'uvicorn.lifespan.on', 'uvicorn.lifespan.off',
    'multipart', 'python_multipart', 'anyio._backends._asyncio',
    'exam_diagnosis.export',
    # QR generation for phone upload picks its image factory by string at call time.
    'qrcode.image.svg',
]
# Packages whose non-Python files must travel with the code: python-docx's default .docx
# template, fontTools' data tables, PyMuPDF's native library (its own hook handles the .pyd;
# collecting the package is belt and braces).
COLLECT_DATA = ['docx', 'fontTools', 'qrcode']
COLLECT_ALL = ['pymupdf']


def datas() -> list[tuple[str, str]]:
    """Project data files: the public knowledge partitions. knowledge.py resolves them relative
    to its own file, which in the bundle lives under _internal/exam_diagnosis/, so `data/knowledge`
    next to the package root is exactly where it will look."""
    knowledge = ROOT / 'data' / 'knowledge'
    return [(str(path), 'data/knowledge') for path in sorted(knowledge.glob('*.y*ml'))]


def pyinstaller_args(output_dir: Path = OUTPUT_DIR, work_dir: Path = WORK_DIR) -> list[str]:
    args = [
        str(ENTRY), '--noconfirm', '--clean', '--onedir', '--console',
        '--name', EXE_NAME,
        '--distpath', str(work_dir / 'dist'), '--workpath', str(work_dir), '--specpath', str(work_dir),
        '--paths', str(ROOT),
    ]
    for module in HIDDEN_IMPORTS:
        args += ['--hidden-import', module]
    for package in COLLECT_DATA:
        args += ['--collect-data', package]
    for package in COLLECT_ALL:
        args += ['--collect-all', package]
    for source, target in datas():
        args += ['--add-data', f'{source}{os.pathsep}{target}']
    return args


def build(output_dir: Path = OUTPUT_DIR, work_dir: Path = WORK_DIR) -> Path:
    import PyInstaller.__main__ as pyinstaller

    _rmtree_with_retries(output_dir)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    pyinstaller.run(pyinstaller_args(output_dir, work_dir))
    # PyInstaller names its output folder after the executable; the shell expects the stable
    # name 'backend'. Copy rather than rename: on Windows a freshly written .exe is routinely
    # held open by antivirus/indexing for a moment and a directory rename then fails outright.
    built = work_dir / 'dist' / EXE_NAME
    shutil.copytree(built, output_dir)
    _rmtree_with_retries(built)
    return executable_path(output_dir)


def _rmtree_with_retries(path: Path, attempts: int = 5) -> None:
    for attempt in range(attempts):
        if not path.exists():
            return
        try:
            shutil.rmtree(path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(1.5)


def executable_path(output_dir: Path = OUTPUT_DIR) -> Path:
    return output_dir / (EXE_NAME + ('.exe' if sys.platform == 'win32' else ''))


# --- smoke test ------------------------------------------------------------------------------------


def _call(base: str, method: str, path: str, body=None):
    request = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None, method=method, headers={'Content-Type': 'application/json', 'X-User-Id': 'local'})
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
        return response.status, response.headers.get('content-type', ''), raw


def exercise_backend(base_url: str) -> dict:
    """Drive a full run through a live backend and return what was verified. Raises on any
    failure. Works against the frozen binary and (in tests) an in-process server alike."""
    status, _, raw = _call(base_url, 'GET', '/api/health')
    assert status == 200 and json.loads(raw)['status'] == 'ok', 'health check failed'
    _, _, raw = _call(base_url, 'GET', '/api/system/status')
    system_status = json.loads(raw)
    partitions = system_status['knowledge']['partitions']
    assert partitions, 'no knowledge partitions — data/knowledge was not bundled'
    # Generating a QR exercises qrcode's lazily-imported SVG factory without opening a LAN port.
    assert system_status['features']['qr_code'], 'QR generation failed — qrcode missing from the bundle?'
    _, _, raw = _call(base_url, 'POST', '/api/submissions', {'stage': 'high', 'subject': '数学', 'province': '广东', 'school_grade': '高二', 'academic_year_label': '25-26'})
    submission = json.loads(raw)
    _, _, raw = _call(base_url, 'POST', f"/api/submissions/{submission['id']}/exams", {'name': '打包冒烟'})
    exam = json.loads(raw)
    _call(base_url, 'POST', f"/api/exams/{exam['id']}/confirm", {'questions': [{'question_number': '1', 'question_text': '求函数定义域', 'is_wrong_question': True, 'original_question_available': True}]})
    _, _, raw = _call(base_url, 'POST', f"/api/submissions/{submission['id']}/analysis-runs", {'exam_ids': [exam['id']]})
    run = json.loads(raw)
    _call(base_url, 'POST', f"/api/analysis-runs/{run['id']}/confirm-start")
    status, _, raw = _call(base_url, 'GET', f"/api/analysis-runs/{run['id']}/report")
    assert status == 200 and json.loads(raw)['wrong_question_count'] == 1, 'report generation failed'
    status, ctype, pdf = _call(base_url, 'GET', f"/api/reports/{run['id']}/export?format=pdf")
    assert status == 200 and pdf.startswith(b'%PDF') and 'pdf' in ctype, 'PDF export failed (PyMuPDF missing from bundle?)'
    status, ctype, docx = _call(base_url, 'GET', f"/api/reports/{run['id']}/export?format=docx")
    assert status == 200 and docx.startswith(b'PK') and 'wordprocessingml' in ctype, 'Word export failed (python-docx template missing from bundle?)'
    return {'partitions': len(partitions), 'qr_code': True, 'pdf_bytes': len(pdf), 'docx_bytes': len(docx)}


def smoke(executable: Path, timeout: float = 60) -> dict:
    """Launch the frozen backend on a free port with a throwaway data dir, wait for LISTENING,
    run exercise_backend, and shut it down."""
    if not executable.is_file():
        raise SystemExit(f'not built: {executable}')
    data_dir = Path(tempfile.mkdtemp(prefix='exam_diagnosis_smoke_'))
    env = {k: v for k, v in os.environ.items() if not (k.startswith('EXAM_DIAGNOSIS_') and k.endswith('_API_KEY'))}
    process = subprocess.Popen([str(executable), '--port', '0', '--data-dir', str(data_dir), '--log-level', 'warning'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, cwd=str(executable.parent))
    base_url = None
    deadline = time.monotonic() + timeout
    try:
        while time.monotonic() < deadline:
            line = process.stdout.readline()
            if not line:
                if process.poll() is not None:
                    raise SystemExit(f'backend exited with {process.returncode} before listening')
                continue
            if line.startswith('LISTENING '):
                base_url = 'http://' + line.split(' ', 1)[1].strip()
                break
        if base_url is None:
            raise SystemExit('backend never announced LISTENING')
        result = exercise_backend(base_url)
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        shutil.rmtree(data_dir, ignore_errors=True)
    return {'base_url': base_url, **result}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split('\n', 1)[0])
    parser.add_argument('--no-smoke', action='store_true')
    parser.add_argument('--smoke-only', action='store_true')
    args = parser.parse_args(argv)
    executable = executable_path()
    if not args.smoke_only:
        executable = build()
        size = sum(path.stat().st_size for path in OUTPUT_DIR.rglob('*') if path.is_file()) / 1024 / 1024
        print(f'built {executable} ({size:.1f} MB on disk)')
    if not args.no_smoke:
        print('smoke:', json.dumps(smoke(executable), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
