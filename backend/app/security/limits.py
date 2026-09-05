"""Small-process limits for the protected demo endpoint.

The demo runs one API process by default. Production deployments with multiple
replicas should move this state to a shared store such as Redis.
"""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date, datetime, timezone
from threading import Lock
from time import monotonic

from fastapi import HTTPException, status

from app.config.business import SecurityConfig


@dataclass
class _Usage:
    requests: deque[float]
    day: date
    estimated_tokens: int = 0


class DemoLimitStore:
    def __init__(self) -> None:
        self._usage: defaultdict[str, _Usage] = defaultdict(
            lambda: _Usage(deque(), datetime.now(timezone.utc).date())
        )
        self._lock = Lock()

    def check_and_reserve(self, user_key: str, content: str, config: SecurityConfig) -> int:
        if len(content) > config.max_input_chars:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail="消息超过长度限制",
            )
        now = monotonic()
        today = datetime.now(timezone.utc).date()
        estimate = max(1, (len(content) + 3) // 4)
        with self._lock:
            usage = self._usage[user_key]
            if usage.day != today:
                usage.day = today
                usage.estimated_tokens = 0
                usage.requests.clear()
            while usage.requests and now - usage.requests[0] >= 60:
                usage.requests.popleft()
            if len(usage.requests) >= config.rate_limit_per_minute:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="请求过于频繁，请稍后再试",
                    headers={"Retry-After": "60"},
                )
            if usage.estimated_tokens + estimate > config.daily_token_budget:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="已达到今日 Token 预算",
                    headers={"Retry-After": "86400"},
                )
            usage.requests.append(now)
            usage.estimated_tokens += estimate
            return config.rate_limit_per_minute - len(usage.requests)

    def reset(self) -> None:
        with self._lock:
            self._usage.clear()


limit_store = DemoLimitStore()


def enforce_chat_limits(
    user_key: str, content: str, config: SecurityConfig
) -> int:
    return limit_store.check_and_reserve(user_key, content, config)
