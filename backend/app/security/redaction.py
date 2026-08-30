"""Local redaction for secrets before model calls or persistence."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RedactionResult:
    text: str
    redacted: bool
    categories: tuple[str, ...]


_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    (
        "api_key",
        re.compile(
            r"(?i)(\b[\w-]*(?:api[_ -]?key|access[_ -]?key)\b\s*[:=]\s*)[^\s,;]+"
        ),
        r"\1[REDACTED]",
    ),
    (
        "token",
        re.compile(r"(?i)(\b(?:bearer\s+|auth[_ -]?token\b\s*[:=]\s*))[^\s,;]+"),
        r"\1[REDACTED]",
    ),
    (
        "password",
        re.compile(
            r"(?i)(\b(?:password|passwd|pwd)\b|密码)\s*[:=：]\s*[^\s,;]+"
        ),
        r"\1[REDACTED]",
    ),
    (
        "secret_key",
        re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{12,}\b"),
        "[REDACTED_SECRET]",
    ),
    (
        "identity_card",
        re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"),
        "[REDACTED_ID]",
    ),
    (
        "phone",
        re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
        "[REDACTED_PHONE]",
    ),
    (
        "bank_card",
        re.compile(r"(?<!\d)(?:\d[ -]?){15,18}\d(?!\d)"),
        "[REDACTED_BANK_CARD]",
    ),
)


def redact_secrets(text: str) -> RedactionResult:
    """Replace recognizable credentials while preserving surrounding context."""

    redacted_text = text
    categories: list[str] = []
    for category, pattern, replacement in _SECRET_PATTERNS:
        if pattern.search(redacted_text):
            categories.append(category)
            redacted_text = pattern.sub(replacement, redacted_text)
    return RedactionResult(
        text=redacted_text,
        redacted=bool(categories),
        categories=tuple(dict.fromkeys(categories)),
    )
