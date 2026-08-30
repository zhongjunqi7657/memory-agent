from app.memory.policy import MemoryCandidate, MemoryDecision, assess_candidate
from app.persistence.models import MemoryKind, MemorySensitivity
from app.security.redaction import redact_secrets


def test_secret_is_redacted_before_storage() -> None:
    result = redact_secrets("我的 api_key=sk-demo-value-1234567890，请不要保存")
    assert result.redacted is True
    assert "sk-demo-value" not in result.text
    assert "[REDACTED]" in result.text


def test_environment_style_api_key_is_redacted() -> None:
    result = redact_secrets("DASHSCOPE_API_KEY=demo-secret-value")
    assert result.redacted is True
    assert "demo-secret-value" not in result.text


def test_explicit_normal_memory_becomes_active() -> None:
    assessment = assess_candidate(
        MemoryCandidate(
            content="用户计划在两个月内完成 Agent 项目",
            kind=MemoryKind.SEMANTIC,
            confidence=0.95,
        )
    )
    assert assessment.decision is MemoryDecision.ACTIVE


def test_inferred_memory_waits_for_confirmation() -> None:
    assessment = assess_candidate(
        MemoryCandidate(
            content="用户可能偏好早晨学习",
            kind=MemoryKind.SEMANTIC,
            confidence=0.91,
            explicit=False,
        )
    )
    assert assessment.decision is MemoryDecision.PENDING


def test_sensitive_memory_waits_for_confirmation() -> None:
    assessment = assess_candidate(
        MemoryCandidate(
            content="用户提到一段健康相关经历",
            kind=MemoryKind.EPISODIC,
            confidence=0.99,
            sensitivity=MemorySensitivity.SENSITIVE,
        )
    )
    assert assessment.decision is MemoryDecision.PENDING
