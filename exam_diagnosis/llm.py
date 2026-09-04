"""Role-based OpenAI-compatible chat client for real model inference.

Mirrors the fallback philosophy of ``OpenAICompatibleVisionParser`` in
``parser.py``: a role that isn't configured, or a call that fails, must never
be reported as a success. Callers get ``None`` back and are expected to fall
back to their deterministic rule-based path — the same rule that keeps OCR
from claiming a fake success when no vision parser is configured.

Four roles are defined today:

- ``analysis``: root-cause diagnosis of a wrong question. Needs real math
  reasoning, defaults to ``deepseek-v4-pro`` (thinking mode).
- ``report``: light narrative/summary writing. Defaults to the cheaper
  ``deepseek-v4-flash``.
- ``split``: splitting a page's extracted text into individual questions.
  Real exam papers use too many incompatible numbering conventions for a
  fixed regex to cover reliably; this is a light classification-shaped task,
  so it also defaults to ``deepseek-v4-flash``.
- ``verify``: generating one real variant practice question (with a fully
  worked reference answer) per error cluster. Getting both the problem and
  its solution right needs the same reasoning tier as ``analysis``, so it
  also defaults to ``deepseek-v4-pro``.
- ``grade``: marking a student's submitted answer to a verification question
  against its reviewed reference answer. Judging partial credit on a worked
  math solution is reasoning work too, so it defaults to ``deepseek-v4-pro``.

Every role is opt-in and independently configurable via environment
variables, e.g. for ``analysis``:

- ``EXAM_DIAGNOSIS_ANALYSIS_API_KEY`` (required to activate the role)
- ``EXAM_DIAGNOSIS_ANALYSIS_BASE_URL`` (defaults to DeepSeek's endpoint)
- ``EXAM_DIAGNOSIS_ANALYSIS_MODEL`` (defaults to ``deepseek-v4-pro``)
- ``EXAM_DIAGNOSIS_ANALYSIS_TIMEOUT`` (seconds, defaults to 30)

Swap in any OpenAI-compatible provider by overriding base_url/model; only the
API key gates whether the role is active at all.
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

DEEPSEEK_BASE_URL = 'https://api.deepseek.com'

ROLE_DEFAULTS = {
    'analysis': {'base_url': DEEPSEEK_BASE_URL, 'model': 'deepseek-v4-pro'},
    'report': {'base_url': DEEPSEEK_BASE_URL, 'model': 'deepseek-v4-flash'},
    'split': {'base_url': DEEPSEEK_BASE_URL, 'model': 'deepseek-v4-flash'},
    'verify': {'base_url': DEEPSEEK_BASE_URL, 'model': 'deepseek-v4-pro'},
    'grade': {'base_url': DEEPSEEK_BASE_URL, 'model': 'deepseek-v4-pro'},
    # Picking knowledge points from a candidate list is classification, not reasoning — the
    # cheaper tier is fine and it runs once per question.
    'match': {'base_url': DEEPSEEK_BASE_URL, 'model': 'deepseek-v4-flash'},
}


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
    defaults = ROLE_DEFAULTS[role]
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
    DeepSeek defaults for that role, matching the vision parser's "must be
    fully configured or stay off" stance, minus needing three vars for every
    role: an operator only has to supply a key to activate real inference.

    `on_call` is optional and purely for observability (see `ChatClient.on_call`); omitting it
    changes nothing about whether or how the role runs.
    """
    if role not in ROLE_DEFAULTS:
        raise ValueError(f'unknown model role: {role}')
    prefix = f'EXAM_DIAGNOSIS_{role.upper()}_'
    api_key = os.getenv(prefix + 'API_KEY')
    if not api_key:
        return None
    defaults = ROLE_DEFAULTS[role]
    base_url = os.getenv(prefix + 'BASE_URL') or defaults['base_url']
    model = os.getenv(prefix + 'MODEL') or defaults['model']
    timeout = float(os.getenv(prefix + 'TIMEOUT') or 30)
    return ChatClient(role=role, base_url=base_url, api_key=api_key, model=model, timeout=timeout, on_call=on_call)
