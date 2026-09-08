from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage

import app.memory.review as review_module
from app.memory.review import ReviewService
from app.persistence.models import MemoryKind, MemoryStatus


class FakeRepository:
    def __init__(self, _session):
        pass

    async def list_for_user(self, _user_id, *, limit):
        assert limit == 200
        now = datetime.now(timezone.utc)
        return [
            SimpleNamespace(
                kind=MemoryKind.SEMANTIC,
                status=MemoryStatus.ACTIVE,
                content="用户偏好先理解原理",
                valid_from=now,
                created_at=now,
                updated_at=now,
            ),
            SimpleNamespace(
                kind=MemoryKind.EPISODIC,
                status=MemoryStatus.ACTIVE,
                content="用户本周完成了 LangGraph 工具练习",
                valid_from=now,
                created_at=now,
                updated_at=now,
            ),
        ]


class FakeReviewModel:
    prompt = ""

    async def ainvoke(self, messages):
        self.prompt = str(messages[-1].content)
        return AIMessage(content="本周完成了工具练习，下一步补充回归测试。")


@pytest.mark.asyncio
async def test_review_uses_governed_memory_facts(monkeypatch) -> None:
    monkeypatch.setattr(review_module, "MemoryRepository", FakeRepository)
    model = FakeReviewModel()

    content = await ReviewService(object(), model=model).generate(
        user_id=uuid4(), period_days=7
    )

    assert "LangGraph 工具练习" in model.prompt
    assert content.startswith("本周完成了工具练习")
