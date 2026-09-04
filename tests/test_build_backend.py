"""The packaging script's non-PyInstaller parts: what it bundles, and the smoke test it runs
against the result (checked here against an in-process server so the checker itself is trusted)."""
import importlib.util
import io
import os
import threading
import time
from pathlib import Path

import pytest

from exam_diagnosis import server

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'build_backend.py'
spec = importlib.util.spec_from_file_location('build_backend', SCRIPT)
build_backend = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build_backend)


def test_bundle_includes_knowledge_partitions_and_runtime_imports(tmp_path):
    datas = build_backend.datas()
    assert datas and all(target == 'data/knowledge' for _, target in datas)
    assert any(source.endswith('high_math_guangdong_v1.yaml') for source, _ in datas)
    args = build_backend.pyinstaller_args(tmp_path / 'out' / 'backend', tmp_path / 'work')
    assert args[0].endswith('backend_entry.py') and '--onedir' in args
    joined = ' '.join(args)
    for needed in ('uvicorn.protocols.http.auto', 'uvicorn.lifespan.on', '--collect-all pymupdf', '--collect-data docx', 'high_math_guangdong_v1.yaml'):
        assert needed in joined
    assert build_backend.executable_path(tmp_path / 'backend').name.startswith('exam-diagnosis-backend')


def test_exercise_backend_passes_against_a_live_server(tmp_path, monkeypatch):
    monkeypatch.setenv('EXAM_DIAGNOSIS_DATA_DIR', str(tmp_path / 'data'))
    port = server.pick_free_port()
    announced = io.StringIO()
    instance = server.build_server('127.0.0.1', port, log_level='warning', announce=announced)
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and 'LISTENING' not in announced.getvalue():
            time.sleep(0.05)
        result = build_backend.exercise_backend(f'http://127.0.0.1:{port}')
        assert result['partitions'] >= 1 and result['pdf_bytes'] > 1000 and result['docx_bytes'] > 1000
    finally:
        instance.should_exit = True
        thread.join(timeout=10)


def test_smoke_refuses_a_missing_executable(tmp_path):
    with pytest.raises(SystemExit, match='not built'):
        build_backend.smoke(tmp_path / 'nope.exe')


def test_smoke_strips_model_keys_from_the_child_environment(monkeypatch, tmp_path):
    # The frozen backend under test must never inherit a real key from the builder's shell.
    monkeypatch.setenv('EXAM_DIAGNOSIS_ANALYSIS_API_KEY', 'sk-real')
    captured = {}

    class FakeProcess:
        def __init__(self, cmd, **kwargs):
            captured['env'] = kwargs['env']
            captured['cmd'] = cmd
            self.stdout = io.StringIO('LISTENING 127.0.0.1:1\n')
            self.returncode = None

        def poll(self):
            return None

        def terminate(self):
            pass

        def wait(self, timeout=None):
            return 0

    exe = tmp_path / 'fake.exe'
    exe.write_bytes(b'')
    monkeypatch.setattr(build_backend.subprocess, 'Popen', FakeProcess)
    monkeypatch.setattr(build_backend, 'exercise_backend', lambda base: {'base': base})
    result = build_backend.smoke(exe)
    assert result['base_url'] == 'http://127.0.0.1:1'
    assert 'EXAM_DIAGNOSIS_ANALYSIS_API_KEY' not in captured['env']
    assert captured['cmd'][:3] == [str(exe), '--port', '0'] and os.path.isabs(captured['cmd'][captured['cmd'].index('--data-dir') + 1])
