from app.protocol.tools import (
    ToolErrorCategory,
    ToolResult,
    ToolStatus,
)


def test_tool_result_factories_keep_stable_statuses() -> None:
    success = ToolResult.success({"value": 1})
    failure = ToolResult.failure(
        code="business_rule",
        category=ToolErrorCategory.BUSINESS,
        message="rejected",
    )

    assert success.status is ToolStatus.SUCCESS
    assert success.content == {"value": 1}
    assert failure.status is ToolStatus.FAILED
    assert failure.error and failure.error.code == "business_rule"
