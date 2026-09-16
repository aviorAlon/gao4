"""scripts/check.py plans the same steps CI runs."""
import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'check.py'
spec = importlib.util.spec_from_file_location('check_script', SCRIPT)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

CI = (Path(__file__).resolve().parents[1] / '.github' / 'workflows' / 'ci.yml').read_text(encoding='utf-8')


def test_plans_backend_and_frontend_steps(monkeypatch):
    monkeypatch.setattr(check, 'npm', lambda: 'npm')
    planned = check.steps()
    assert [name for name, _, _ in planned] == ['backend tests', 'frontend lint', 'frontend type-check + build']
    assert planned[0][1] == [sys.executable, '-m', 'pytest', '-q'] and planned[0][2] == check.ROOT
    assert planned[1][1] == ['npm', 'run', 'lint'] and planned[2][1] == ['npm', 'run', 'build']
    assert all(cwd == check.FRONTEND for _, _, cwd in planned[1:])


def test_can_run_one_side_only(monkeypatch):
    monkeypatch.setattr(check, 'npm', lambda: 'npm')
    assert [n for n, _, _ in check.steps(frontend=False)] == ['backend tests']
    assert [n for n, _, _ in check.steps(backend=False)] == ['frontend lint', 'frontend type-check + build']


def test_summary_exit_code_reflects_failures(monkeypatch):
    monkeypatch.setattr(check, 'steps', lambda backend, frontend: [('a', ['x'], check.ROOT), ('b', ['y'], check.ROOT)])
    codes = iter([0, 1])
    monkeypatch.setattr(check.subprocess, 'call', lambda command, cwd: next(codes))
    assert check.main([]) == 1
    codes = iter([0, 0])
    assert check.main([]) == 0


def test_missing_npm_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(check.shutil, 'which', lambda name: None)
    with pytest.raises(SystemExit, match='npm not found'):
        check.npm()


def test_ci_runs_the_same_commands():
    for command in ('python -m pytest -q', 'npm run lint', 'npm run build', 'npm ci'):
        assert command in CI
    assert 'windows-latest' in CI and 'ubuntu-latest' in CI  # the desktop build ships on Windows
