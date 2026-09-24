"""Runtime settings stored in <data_dir>/config.json: model-role credentials, the provider-wide
model defaults and the cost budget.

The web deployment configures these through environment variables / .env.local, and that keeps
working unchanged. This module exists for the desktop build, where "edit a dotfile and set env
vars" is not something a user will do: the settings page writes config.json, and its values are
pushed into os.environ so every existing reader (llm.client_from_env, the vision parser, the
budget gate) sees them without knowing where they came from.

Precedence: a value already present in the environment (a real env var, or .env.local, which
loads into the environment first) always wins over config.json. Values this module put into the
environment are remembered in _APPLIED so they can be replaced or removed on the next save —
and so "was this set by the operator's environment or by the settings page" can be reported
honestly (the UI locks fields the environment owns).
"""
from __future__ import annotations

import json
import os
import stat
import tempfile
from pathlib import Path
from typing import Any

from . import paths
from .llm import BUILTIN_PROVIDER, PROVIDER_ENV, ROLE_DEFAULTS, ROLE_PURPOSES, role_defaults

VISION_ROLE = 'vision'
CONFIGURABLE_ROLES = tuple(ROLE_DEFAULTS) + (VISION_ROLE,)
ROLE_FIELDS = ('api_key', 'base_url', 'model', 'timeout')
# Provider-wide defaults every role falls back to (see llm.role_defaults); config.json section 'provider'.
PROVIDER_FIELDS = tuple(PROVIDER_ENV)
BUDGET_FIELDS = {
    'daily_token_budget': 'EXAM_DIAGNOSIS_MODEL_DAILY_TOKEN_BUDGET',
    'max_calls_per_minute': 'EXAM_DIAGNOSIS_MODEL_MAX_CALLS_PER_MINUTE',
}
ROLE_PURPOSE = {**ROLE_PURPOSES, VISION_ROLE: '图片 / 扫描 PDF 视觉解析'}
DEFAULT_TIMEOUT = {VISION_ROLE: 60.0}

_APPLIED: set[str] = set()


def env_name(role: str, field: str) -> str:
    return f'EXAM_DIAGNOSIS_{role.upper()}_{field.upper()}'


def load_config(path: Path | None = None) -> dict[str, Any]:
    file = path or paths.config_path()
    if not file.exists():
        return {}
    try:
        loaded = json.loads(file.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def save_config(config: dict[str, Any], path: Path | None = None) -> Path:
    """Write atomically (temp file + replace) and keep the file owner-readable only where the OS
    supports it — it holds API keys."""
    file = path or paths.config_path()
    file.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix='config.', suffix='.json.tmp', dir=file.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump(config, handle, ensure_ascii=False, indent=2)
        try:
            os.chmod(tmp_name, stat.S_IRUSR | stat.S_IWUSR)
        except OSError:
            pass
        os.replace(tmp_name, file)
    finally:
        if os.path.exists(tmp_name):
            os.unlink(tmp_name)
    return file


def apply_config(config: dict[str, Any]) -> None:
    """Push config.json values into the environment, without ever overriding a value the
    environment already had from somewhere else."""
    for name in list(_APPLIED):
        os.environ.pop(name, None)
        _APPLIED.discard(name)
    for role, fields in (config.get('roles') or {}).items():
        if role not in CONFIGURABLE_ROLES or not isinstance(fields, dict):
            continue
        for field in ROLE_FIELDS:
            value = fields.get(field)
            if value in (None, ''):
                continue
            name = env_name(role, field)
            # An empty environment value counts as unset (every reader treats '' as "off"), so
            # config.json may fill it — otherwise `EXAM_DIAGNOSIS_X_API_KEY=` in a shell would
            # silently make the settings page inert.
            if not os.environ.get(name):
                os.environ[name] = str(value)
                _APPLIED.add(name)
    for field in PROVIDER_FIELDS:
        value = (config.get('provider') or {}).get(field)
        if value in (None, ''):
            continue
        name = PROVIDER_ENV[field]
        if not os.environ.get(name):
            os.environ[name] = str(value)
            _APPLIED.add(name)
    for key, name in BUDGET_FIELDS.items():
        value = (config.get('budget') or {}).get(key)
        if value in (None, '', 0):
            continue
        if not os.environ.get(name):
            os.environ[name] = str(int(value))
            _APPLIED.add(name)


def _source(name: str) -> str | None:
    if name in _APPLIED:
        return 'config'
    return 'env' if os.getenv(name) else None


def describe() -> dict[str, Any]:
    """Current effective settings for the UI. Keys are reported as set/unset plus where they came
    from — the key itself never leaves the process."""
    roles = []
    for role in CONFIGURABLE_ROLES:
        defaults = role_defaults(role) if role in ROLE_DEFAULTS else {'base_url': None, 'model': None}
        key_name = env_name(role, 'api_key')
        entry: dict[str, Any] = {
            'role': role, 'purpose': ROLE_PURPOSE.get(role, role),
            'api_key_set': bool(os.getenv(key_name)), 'api_key_source': _source(key_name),
        }
        for field in ('base_url', 'model', 'timeout'):
            name = env_name(role, field)
            raw = os.getenv(name)
            if field == 'timeout':
                try:
                    value: Any = float(raw) if raw else None
                except ValueError:
                    value = None
                entry['timeout_default'] = DEFAULT_TIMEOUT.get(role, 30.0)
            else:
                value = raw or None
                entry[f'{field}_default'] = defaults.get(field)
            entry[field] = value
            entry[f'{field}_source'] = _source(name) if raw else None
        effective_base = entry['base_url'] or entry['base_url_default']
        effective_model = entry['model'] or entry['model_default']
        entry['configured'] = entry['api_key_set'] and bool(effective_base and effective_model)
        entry['locked'] = entry['api_key_source'] == 'env'
        roles.append(entry)
    budget = {}
    for key, name in BUDGET_FIELDS.items():
        raw = os.getenv(name)
        try:
            budget[key] = int(raw) if raw else None
        except ValueError:
            budget[key] = None
        budget[f'{key}_source'] = _source(name) if raw else None
    provider: dict[str, Any] = {}
    for field, name in PROVIDER_ENV.items():
        raw = os.getenv(name)
        provider[field] = raw or None
        provider[f'{field}_source'] = _source(name) if raw else None
        provider[f'{field}_default'] = BUILTIN_PROVIDER[field]
    return {'roles': roles, 'provider': provider, 'budget': budget, 'config_path': str(paths.config_path())}


def provider_status() -> dict[str, Any]:
    """The provider-wide defaults as every role currently resolves them, each tagged with where it
    came from: 'env', 'config' (settings page) or 'builtin'."""
    status: dict[str, Any] = {}
    for field, name in PROVIDER_ENV.items():
        raw = os.getenv(name)
        status[field] = raw or BUILTIN_PROVIDER[field]
        status[f'{field}_source'] = (_source(name) if raw else None) or 'builtin'
    return status


def locked_fields(role_changes: dict[str, Any], budget_changes: dict[str, Any], provider_changes: dict[str, Any] | None = None) -> list[str]:
    """Env-owned variables the caller is trying to change — a config.json value would never take
    effect over them, so refusing is more honest than silently saving something inert."""
    locked = []
    for role, fields in (role_changes or {}).items():
        for field in (fields or {}):
            name = env_name(role, field)
            if os.getenv(name) and name not in _APPLIED:
                locked.append(name)
    for key in (budget_changes or {}):
        name = BUDGET_FIELDS.get(key)
        if name and os.getenv(name) and name not in _APPLIED:
            locked.append(name)
    for field in (provider_changes or {}):
        name = PROVIDER_ENV.get(field)
        if name and os.getenv(name) and name not in _APPLIED:
            locked.append(name)
    return locked


def merge_changes(config: dict[str, Any], changes: dict[str, Any]) -> dict[str, Any]:
    """Apply a partial update: a field that's omitted is kept, one sent as ''/null is cleared.
    Raises ValueError for anything malformed."""
    merged = json.loads(json.dumps(config))  # deep copy of plain JSON
    for role, fields in (changes.get('roles') or {}).items():
        if role not in CONFIGURABLE_ROLES:
            raise ValueError(f'unknown model role: {role}')
        if not isinstance(fields, dict):
            raise ValueError(f'role settings must be an object: {role}')
        target = merged.setdefault('roles', {}).setdefault(role, {})
        for field, value in fields.items():
            if field not in ROLE_FIELDS:
                raise ValueError(f'unknown field {field} for role {role}')
            if value in (None, ''):
                target.pop(field, None)
                continue
            if field == 'timeout':
                try:
                    value = float(value)
                except (TypeError, ValueError):
                    raise ValueError(f'timeout must be a number ({role})') from None
                if value <= 0:
                    raise ValueError(f'timeout must be positive ({role})')
                target[field] = value
            else:
                if not isinstance(value, str):
                    raise ValueError(f'{field} must be a string ({role})')
                target[field] = value.strip()
        if not target:
            merged['roles'].pop(role, None)
    provider_changes = changes.get('provider') or {}
    if not isinstance(provider_changes, dict):
        raise ValueError('provider settings must be an object')
    for field, value in provider_changes.items():
        if field not in PROVIDER_FIELDS:
            raise ValueError(f'unknown provider field: {field}')
        target = merged.setdefault('provider', {})
        if value in (None, ''):
            target.pop(field, None)
        elif not isinstance(value, str):
            raise ValueError(f'provider {field} must be a string')
        else:
            target[field] = value.strip()
        if not target:
            merged.pop('provider', None)
    for key, value in (changes.get('budget') or {}).items():
        if key not in BUDGET_FIELDS:
            raise ValueError(f'unknown budget field: {key}')
        budget = merged.setdefault('budget', {})
        if value in (None, '', 0):
            budget.pop(key, None)
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) <= 0:
            raise ValueError(f'{key} must be a positive integer')
        budget[key] = int(value)
    return merged
