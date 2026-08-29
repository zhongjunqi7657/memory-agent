"""Local redaction for secrets before model calls or persistence."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RedactionResult:
    text: str
    redacted: bool
    categories: tuple[str, ...]


_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "api_key",
        re.compile(r"(?i)(\b(?:api[_ -]?key|access[_ -]?key)\b\s*[:=]\s*)[^\s,;]+"),
    ),
    (
        "token",
        re.compile(r"(?i)(\b(?:bearer\s+|auth[_ -]?token\b\s*[:=]\s*))[^\s,;]+"),
    ),
    (
        "password",
        re.compile(r"(?i)(\b(?:password|passwd|pwd)\b\s*[:=]\s*)[^\s,;]+"),
    ),
    (
        "secret_key",
        re.compile(r"(?i)\bsk-[A-Za-z0-9_-]{12,}\b"),
    ),
)


def redact_secrets(text: str) -> RedactionResult:
    """Replace recognizable credentials while preserving surrounding context."""

    redacted_text = text
    categories: list[str] = []
    for category, pattern in _SECRET_PATTERNS:
        if pattern.search(redacted_text):
            categories.append(category)
            if category == "secret_key":
                redacted_text = pattern.sub("[REDACTED_SECRET]", redacted_text)
            else:
                redacted_text = pattern.sub(r"\1[REDACTED]", redacted_text)
    return RedactionResult(
        text=redacted_text,
        redacted=bool(categories),
        categories=tuple(dict.fromkeys(categories)),
    )
