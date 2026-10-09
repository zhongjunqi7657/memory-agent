"""Legacy HTTP result compatibility after migration to the unified executor."""

from uuid import uuid4

from app.mcp.transport import MCPCallResponse
from app.protocol.tools import ToolErrorCategory, ToolResult


def test_legacy_success_keeps_text_content_and_response_fields():
    run_id = uuid4()
    result = MCPCallResponse.from_result(run_id, ToolResult.success([{"id": 1}]), "search_memory")
    assert result.model_dump() == {
        "run_id": run_id, "tool": "search_memory", "ok": True,
        "content": '[{"id": 1}]', "error": None,
    }


def test_legacy_internal_error_keeps_execution_failed_code():
    result = MCPCallResponse.from_result(uuid4(), ToolResult.failure(
        code="internal_error", category=ToolErrorCategory.INTERNAL, message="工具执行失败",
    ), "search_memory")
    assert result.ok is False
    assert result.content is None
    assert result.error == {"code": "execution_failed", "message": "工具执行失败"}


def test_legacy_disabled_error_code_is_compatible():
    result = MCPCallResponse.from_result(uuid4(), ToolResult.failure(
        code="tools_disabled", category=ToolErrorCategory.PERMISSION, message="工具调用已禁用",
    ), "search_memory")
    assert result.error["code"] == "mcp_disabled"
