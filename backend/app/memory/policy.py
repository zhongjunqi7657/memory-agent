"""Deterministic policy for turning model candidates into memory decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.config.business import MemoryConfig, get_business_config
from app.persistence.models import MemoryKind, MemorySensitivity
from app.security.redaction import redact_secrets


class MemoryDecision(str, Enum):
    ACTIVE = "active"
    PENDING = "pending"
    REJECT = "reject"


@dataclass(frozen=True)
class MemoryCandidate:
    content: str
    kind: MemoryKind
    confidence: float
    sensitivity: MemorySensitivity = MemorySensitivity.NORMAL
    explicit: bool = True
    canonical_key: str | None = None


@dataclass(frozen=True)
class MemoryAssessment:
    decision: MemoryDecision
    content: str
    reason: str
    sensitivity: MemorySensitivity
    confidence: float


def assess_candidate(
    candidate: MemoryCandidate, *, config: MemoryConfig | None = None
) -> MemoryAssessment:
    """Apply secret, sensitivity, explicitness and confidence rules in that order."""

    business = config or get_business_config().memory
    redaction = redact_secrets(candidate.content)
    if candidate.sensitivity is MemorySensitivity.SECRET or redaction.redacted:
        return MemoryAssessment(
            decision=MemoryDecision.REJECT,
            content=redaction.text,
            reason="检测到私密凭据，不写入长期记忆",
            sensitivity=MemorySensitivity.SECRET,
            confidence=candidate.confidence,
        )
    if (
        candidate.sensitivity is MemorySensitivity.SENSITIVE
        or not candidate.explicit
        or candidate.confidence < business.active_confidence_threshold
    ):
        reason = "敏感信息或模型推断，等待用户在记忆面板确认"
        if candidate.confidence < business.pending_confidence_threshold:
            reason = "置信度较低，暂存为待确认记忆"
        return MemoryAssessment(
            decision=MemoryDecision.PENDING,
            content=candidate.content,
            reason=reason,
            sensitivity=candidate.sensitivity,
            confidence=candidate.confidence,
        )
    return MemoryAssessment(
        decision=MemoryDecision.ACTIVE,
        content=candidate.content,
        reason="用户明确表达且置信度达到自动生效阈值",
        sensitivity=candidate.sensitivity,
        confidence=candidate.confidence,
    )
