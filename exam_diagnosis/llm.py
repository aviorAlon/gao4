"""Role-based OpenAI-compatible chat client for real model inference.

Mirrors the fallback philosophy of ``OpenAICompatibleVisionParser`` in
``parser.py``: a role that isn't configured, or a call that fails, must never
be reported as a success. Callers get ``None`` back and are expected to fall
back to their deterministic rule-based path — the same rule that keeps OCR
from claiming a fake success when no vision parser is configured.

Six roles are defined, in two tiers:

- ``analysis`` (root-cause diagnosis of a wrong question), ``verify``
  (generating one real variant practice question with a fully worked reference
  answer per error cluster) and ``grade`` (marking a student's submitted answer
  against its reviewed reference answer) need real math reasoning — the
  **reasoning** tier.
- ``report`` (narrative summary), ``split`` (cutting a page's extracted text
  into individual questions; real exam papers use too many incompatible
  numbering conventions for a fixed regex) and ``match`` (picking knowledge
  points from a candidate list) are light, classification-shaped work — the
  cheaper **fast** tier.

Every role is opt-in and independently configurable via environment
variables, e.g. for ``analysis``:

- ``EXAM_DIAGNOSIS_ANALYSIS_API_KEY`` (required to activate the role)
- ``EXAM_DIAGNOSIS_ANALYSIS_BASE_URL``
- ``EXAM_DIAGNOSIS_ANALYSIS_MODEL``
- ``EXAM_DIAGNOSIS_ANALYSIS_TIMEOUT`` (seconds, defaults to 30)

``BASE_URL`` / ``MODEL`` resolve in three steps: the per-role variable, then
the provider-wide defaults ``EXAM_DIAGNOSIS_DEFAULT_BASE_URL`` /
``EXAM_DIAGNOSIS_DEFAULT_REASONING_MODEL`` / ``EXAM_DIAGNOSIS_DEFAULT_FAST_MODEL``
(one place to point every role at a different OpenAI-compatible provider),
then the built-in profile in ``BUILTIN_PROVIDER``. Only the API key gates
whether the role is active at all.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

# The provider every role talks to when nothing else is configured. It is a plain
# OpenAI-compatible profile, not a hard dependency: override it for all roles at once with the
# EXAM_DIAGNOSIS_DEFAULT_* variables (or the "default provider" section of the settings page), or
# per role with EXAM_DIAGNOSIS_<ROLE>_BASE_URL / _MODEL.
BUILTIN_PROVIDER = {
    'base_url': 'https://api.deepseek.com',
    'reasoning_model': 'deepseek-v4-pro',
    'fast_model': 'deepseek-v4-flash',
}
PROVIDER_ENV = {
    'base_url': 'EXAM_DIAGNOSIS_DEFAULT_BASE_URL',
    'reasoning_model': 'EXAM_DIAGNOSIS_DEFAULT_REASONING_MODEL',
    'fast_model': 'EXAM_DIAGNOSIS_DEFAULT_FAST_MODEL',
}
# Which tier each role wants. Diagnosing a wrong answer, writing a correct worked solution and
# grading one are reasoning work; splitting text, summarising numbers and picking knowledge points
# from a candidate list are classification-shaped and run fine on the cheaper tier.
ROLE_TIERS = {
    'analysis': 'reasoning', 'verify': 'reasoning', 'grade': 'reasoning',
    'report': 'fast', 'split': 'fast', 'match': 'fast',
}
# The role registry (which roles exist) with the built-in defaults — the values here are what a
# fresh install uses; `role_defaults()` is what the running process actually resolves to.
ROLE_DEFAULTS = {
    role: {'base_url': BUILTIN_PROVIDER['base_url'], 'model': BUILTIN_PROVIDER[f'{tier}_model']}
    for role, tier in ROLE_TIERS.items()
}


def provider_defaults() -> dict[str, Any]:
    """The provider-wide defaults as currently effective: each field's value, and whether it came
    from the environment or is the built-in profile."""
    result: dict[str, Any] = {}
    for field, name in PROVIDER_ENV.items():
        raw = os.getenv(name)
        result[field] = raw or BUILTIN_PROVIDER[field]
        result[f'{field}_source'] = 'env' if raw else 'builtin'
        result[f'{field}_builtin'] = BUILTIN_PROVIDER[field]
    return result


def role_defaults(role: str) -> dict[str, str]:
    """base_url/model a role falls back to when its own EXAM_DIAGNOSIS_<ROLE>_* variables are unset:
    the provider-wide environment defaults if given, else the built-in profile."""
    if role not in ROLE_TIERS:
        raise ValueError(f'unknown model role: {role}')
    provider = provider_defaults()
    return {'base_url': provider['base_url'], 'model': provider[f"{ROLE_TIERS[role]}_model"]}


@dataclass
class ChatClient:
    """One configured model role, bound to a single OpenAI-compatible endpoint.

    `on_call`, when given, is invoked once per `complete_json` call (not per retry attempt) with a
    structured record — role, model, latency, whether it ultimately succeeded, the last error, how
    many attempts it took, and token usage if the provider reported it. This is the only place that
    knows all of that; without it, every model call was a black box (no way to tell success rate,
    latency, cost, or retry pressure apart from reading server logs by hand).
    """

    role: str
    base_url: str
    api_key: str
    model: str
    timeout: float = 30.0
    max_retries: int = 2
    on_call: Callable[[dict[str, Any]], None] | None = field(default=None, compare=False)

    def complete_json(self, system: str, user: str) -> dict[str, Any] | None:
        """Ask the model for a strict JSON object. Returns None on any failure — never raises."""
        payload = {
            'model': self.model, 'temperature': 0,
            'response_format': {'type': 'json_object'},
            'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}],
        }
        endpoint = self.base_url.rstrip('/') + '/chat/completions'
        request = urllib.request.Request(
            endpoint, data=json.dumps(payload).encode(), method='POST',
            headers={'Authorization': f'Bearer {self.api_key}', 'Content-Type': 'application/json'},
        )
        started = time.monotonic()
        last_error: str | None = None
        usage: dict[str, Any] | None = None
        for attempt in range(self.max_retries):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    outer = json.loads(response.read().decode())
                usage = outer.get('usage') if isinstance(outer.get('usage'), dict) else None
                result = _extract_json(outer)
                self._record(started, attempt + 1, result is not None, None if result is not None else 'response_not_valid_json_object', usage)
                return result
            except Exception as exc:
                last_error = f'{type(exc).__name__}: {exc}'
                if attempt < self.max_retries - 1:
                    time.sleep(0.05)
        self._record(started, self.max_retries, False, last_error, usage)
        return None

    def _record(self, started: float, attempts: int, success: bool, error: str | None, usage: dict[str, Any] | None) -> None:
        if self.on_call is None:
            return
        try:
            self.on_call({
                'role': self.role, 'model': self.model, 'base_url': self.base_url,
                'latency_ms': round((time.monotonic() - started) * 1000),
                'attempts': attempts, 'success': success, 'error': error,
                'prompt_tokens': (usage or {}).get('prompt_tokens'),
                'completion_tokens': (usage or {}).get('completion_tokens'),
                'total_tokens': (usage or {}).get('total_tokens'),
            })
        except Exception:
            pass  # observability must never be the reason a real model call looks like it failed


def _extract_json(outer: dict[str, Any]) -> dict[str, Any] | None:
    content = outer.get('choices', [{}])[0].get('message', {}).get('content')
    if isinstance(content, list):
        content = ''.join(part.get('text', '') for part in content if isinstance(part, dict) and part.get('type', 'text') == 'text')
    if not isinstance(content, str):
        return None
    content = content.strip()
    if content.startswith('```'):
        content = re.sub(r'^```(?:json)?\s*|\s*```$', '', content, flags=re.IGNORECASE).strip()
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None


ROLE_PURPOSES = {
    'split': '题目切分', 'analysis': '错因诊断', 'verify': '验证题生成',
    'grade': '验证题批改', 'report': '报告概述', 'match': '知识点语义匹配',
}


def role_status(role: str) -> dict[str, Any]:
    """What an operator needs to know about a role — is it live, and with what — without the
    key itself ever leaving the process (only whether one is set)."""
    if role not in ROLE_DEFAULTS:
        raise ValueError(f'unknown model role: {role}')
    prefix = f'EXAM_DIAGNOSIS_{role.upper()}_'
    defaults = role_defaults(role)
    return {
        'role': role,
        'purpose': ROLE_PURPOSES.get(role, role),
        'configured': bool(os.getenv(prefix + 'API_KEY')),
        'base_url': os.getenv(prefix + 'BASE_URL') or defaults['base_url'],
        'model': os.getenv(prefix + 'MODEL') or defaults['model'],
        'timeout': float(os.getenv(prefix + 'TIMEOUT') or 30),
        'env_prefix': prefix,
    }


def client_from_env(role: str, on_call: Callable[[dict[str, Any]], None] | None = None) -> ChatClient | None:
    """Build a ChatClient for `role` from EXAM_DIAGNOSIS_<ROLE>_* env vars.

    Only the API key is required to opt in — base_url/model fall back to the
    provider defaults for that role's tier (see `role_defaults`), matching the
    vision parser's "must be fully configured or stay off" stance, minus needing
    three vars for every role: an operator only has to supply a key to activate
    real inference.

    `on_call` is optional and purely for observability (see `ChatClient.on_call`); omitting it
    changes nothing about whether or how the role runs.
    """
    if role not in ROLE_DEFAULTS:
        raise ValueError(f'unknown model role: {role}')
    prefix = f'EXAM_DIAGNOSIS_{role.upper()}_'
    api_key = os.getenv(prefix + 'API_KEY')
    if not api_key:
        return None
    defaults = role_defaults(role)
    base_url = os.getenv(prefix + 'BASE_URL') or defaults['base_url']
    model = os.getenv(prefix + 'MODEL') or defaults['model']
    timeout = float(os.getenv(prefix + 'TIMEOUT') or 30)
    return ChatClient(role=role, base_url=base_url, api_key=api_key, model=model, timeout=timeout, on_call=on_call)
