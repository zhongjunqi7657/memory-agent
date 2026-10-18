"""Reject credentials in structured tool arguments before persisting a review."""

import re

from app.security.redaction import redact_secrets

SECRET_KEYS = re.compile(r"^(?:password|passwd|pwd|secret|token|access_token|refresh_token|api_key|apikey|authorization|credential|credentials)$", re.IGNORECASE)
SECRET_FLAG = re.compile(r"(?:^|\s)--?(?:password|passwd|token|api[-_]key|secret)(?:\s|=)", re.IGNORECASE)


def contains_secret(value) -> bool:
    if isinstance(value, dict):
        return any((SECRET_KEYS.fullmatch(str(key)) and bool(child)) or contains_secret(child) for key, child in value.items())
    if isinstance(value, (list, tuple)):
        return any(contains_secret(child) for child in value) or bool(SECRET_FLAG.search(" ".join(str(child) for child in value)))
    return isinstance(value, str) and redact_secrets(value).redacted
