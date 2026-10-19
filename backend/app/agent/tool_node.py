"""Convert model calls and unified results at the LangGraph boundary."""

import json

from langchain_core.messages import ToolMessage

from app.protocol.tools import ToolCall, ToolExecutionContext, ToolResult, ToolStatus
from app.tools.executor import ToolExecutor


def tool_message(call_id: str, name: str, result: ToolResult) -> ToolMessage:
    failed = result.status is not ToolStatus.SUCCESS
    if failed:
        content = {
            "status": result.status.value,
            "error": {
                "code": result.error.code,
                "category": result.error.category.value,
                "message": result.error.message,
                "retryable": result.error.retryable,
            } if result.error else {"code": "internal_error", "message": "工具执行失败"},
        }
        if result.content is not None:
            content["content"] = result.content
    else:
        content = result.content
    if "structured_content" in result.metadata:
        content = {"content": content, "structured_content": result.metadata["structured_content"]}
    return ToolMessage(
        content=content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
        tool_call_id=call_id, name=name, status="error" if failed else "success",
    )


class UnifiedToolNode:
    def __init__(self, executor: ToolExecutor, context: ToolExecutionContext) -> None:
        self.executor = executor
        self.context = context

    async def __call__(self, state) -> dict[str, object]:
        messages = []
        # Request tools share one SQLAlchemy session: execute sequentially.
        for call in state["messages"][-1].tool_calls:
            result = await self.executor.execute(
                ToolCall(call["id"], call["name"], call["args"]), self.context
            )
            messages.append(tool_message(call["id"], call["name"], result))
        return {"messages": messages, "tool_rounds": state.get("tool_rounds", 0) + 1}
