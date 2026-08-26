"""Role-based OpenAI-compatible chat client for real model inference.

Mirrors the fallback philosophy of ``OpenAICompatibleVisionParser`` in
``parser.py``: a role that isn't configured, or a call that fails, must never
be reported as a success. Callers get ``None`` back and are expected to fall
back to their deterministic rule-based path — the same rule that keeps OCR
from claiming a fake success when no vision parser is configured.

Two roles are defined today:

- ``analysis``: root-cause diagnosis of a wrong question. Needs real math
  reasoning, defaults to ``deepseek-v4-pro`` (thinking mode).
- ``report``: light narrative/summary writing. Defaults to the cheaper
  ``deepseek-v4-flash``.

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
from dataclasses import dataclass
from typing import Any

DEEPSEEK_BASE_URL = 'https://api.deepseek.com'

ROLE_DEFAULTS = {
    'analysis': {'base_url': DEEPSEEK_BASE_URL, 'model': 'deepseek-v4-pro'},
    'report': {'base_url': DEEPSEEK_BASE_URL, 'model': 'deepseek-v4-flash'},
}


@dataclass
class ChatClient:
    """One configured model role, bound to a single OpenAI-compatible endpoint."""

    role: str
    base_url: str
    api_key: str
    model: str
    timeout: float = 30.0
    max_retries: int = 2

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
        for attempt in range(self.max_retries):
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    outer = json.loads(response.read().decode())
                return _extract_json(outer)
            except Exception:
                if attempt < self.max_retries - 1:
                    time.sleep(0.05)
        return None


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


def client_from_env(role: str) -> ChatClient | None:
    """Build a ChatClient for `role` from EXAM_DIAGNOSIS_<ROLE>_* env vars.

    Only the API key is required to opt in — base_url/model fall back to the
    DeepSeek defaults for that role, matching the vision parser's "must be
    fully configured or stay off" stance, minus needing three vars for every
    role: an operator only has to supply a key to activate real inference.
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
    return ChatClient(role=role, base_url=base_url, api_key=api_key, model=model, timeout=timeout)
