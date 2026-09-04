"""Account and session primitives: password hashing, bearer tokens, login throttling.

Pure helpers with no storage of their own — app.py owns the USERS / AUTH_SESSIONS mappings and
decides when a request must carry a token (see EXAM_DIAGNOSIS_AUTH_REQUIRED there). Everything
here is stdlib: pbkdf2 for passwords, secrets for tokens, hmac.compare_digest for comparisons.
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import time
from datetime import datetime, timedelta

PBKDF2_ITERATIONS = 200_000
USERNAME_RE = re.compile(r'^[a-z0-9][a-z0-9_.-]{2,31}$')
MIN_PASSWORD_LENGTH = 8


def normalize_username(raw: str | None) -> str:
    return (raw or '').strip().lower()


def validate_credentials(username: str, password: str) -> str | None:
    """None if acceptable, else the error code to return."""
    if not USERNAME_RE.fullmatch(username):
        return 'invalid_username'
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        return 'password_too_short'
    return None


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, PBKDF2_ITERATIONS)
    return f'pbkdf2_sha256${PBKDF2_ITERATIONS}${salt.hex()}${digest.hex()}'


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, iterations, salt_hex, digest_hex = stored.split('$')
        if scheme != 'pbkdf2_sha256':
            return False
        candidate = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), bytes.fromhex(salt_hex), int(iterations))
        return hmac.compare_digest(candidate, bytes.fromhex(digest_hex))
    except (ValueError, AttributeError):
        return False


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_key(token: str) -> str:
    """Sessions are stored under the token's hash, not the token: a copy of the database must not
    be a bag of live credentials."""
    return hashlib.sha256(token.encode('utf-8')).hexdigest()


def bearer_token(header_value: str | None) -> str | None:
    if not header_value:
        return None
    scheme, _, token = header_value.strip().partition(' ')
    if scheme.lower() != 'bearer' or not token.strip():
        return None
    return token.strip()


def expiry(ttl_hours: float) -> str:
    return (datetime.utcnow() + timedelta(hours=ttl_hours)).isoformat() + 'Z'


def is_expired(session: dict, now: datetime | None = None) -> bool:
    try:
        expires_at = datetime.fromisoformat(str(session.get('expires_at', '')).rstrip('Z'))
    except ValueError:
        return True
    return (now or datetime.utcnow()) >= expires_at


class LoginThrottle:
    """Per-username failed-login counter: after `max_failures` failures inside `window_seconds`,
    further attempts are refused until the window slides. In-memory on purpose — it only has to
    slow down guessing on one running process, not survive a restart."""

    def __init__(self, max_failures: int = 5, window_seconds: float = 900):
        self.max_failures = max_failures
        self.window_seconds = window_seconds
        self._failures: dict[str, list[float]] = {}

    def _prune(self, key: str, now: float) -> list[float]:
        recent = [stamp for stamp in self._failures.get(key, []) if now - stamp < self.window_seconds]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def blocked(self, key: str) -> bool:
        return len(self._prune(key, time.monotonic())) >= self.max_failures

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        self._prune(key, now)
        self._failures.setdefault(key, []).append(now)

    def clear(self, key: str) -> None:
        self._failures.pop(key, None)

    def reset(self) -> None:
        self._failures.clear()
