import pytest
from fastapi import HTTPException

from app.config.business import SecurityConfig
from app.security.limits import DemoLimitStore


def test_rate_limit_rejects_the_next_request() -> None:
    store = DemoLimitStore()
    config = SecurityConfig(rate_limit_per_minute=2, daily_token_budget=100)

    assert store.check_and_reserve("user-a", "hello", config) == 1
    assert store.check_and_reserve("user-a", "hello", config) == 0
    with pytest.raises(HTTPException) as error:
        store.check_and_reserve("user-a", "hello", config)

    assert error.value.status_code == 429
    assert "频繁" in str(error.value.detail)


def test_daily_token_budget_is_scoped_per_user() -> None:
    store = DemoLimitStore()
    config = SecurityConfig(rate_limit_per_minute=10, daily_token_budget=100)

    store.check_and_reserve("user-a", "x" * 400, config)
    with pytest.raises(HTTPException) as error:
        store.check_and_reserve("user-a", "x", config)
    assert error.value.status_code == 429

    assert store.check_and_reserve("user-b", "x", config) == 9


def test_oversized_input_is_rejected_before_consuming_quota() -> None:
    store = DemoLimitStore()
    config = SecurityConfig(
        max_input_chars=100, rate_limit_per_minute=1, daily_token_budget=100
    )

    with pytest.raises(HTTPException) as error:
        store.check_and_reserve("user-a", "x" * 101, config)
    assert error.value.status_code == 413

    assert store.check_and_reserve("user-a", "ok", config) == 0
