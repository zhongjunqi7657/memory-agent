"""Finish a bounded tool run with findings instead of discarding its evidence."""

from langchain_core.messages import AIMessage, SystemMessage, ToolMessage


async def summarize_tool_limit(chat_model, system_message, messages, rounds):
    blocked = [ToolMessage(
        content="工具执行预算已用完，此调用未执行。", tool_call_id=call["id"], name=call["name"], status="error",
    ) for call in messages[-1].tool_calls]
    response = await chat_model.ainvoke([
        system_message, *messages, *blocked,
        SystemMessage(content=(
            f"本次工具执行已达到 {rounds} 轮上限，所有待执行的调用均未执行。现在仅根据已有工具结果生成阶段性回答。"
            "禁止请求新工具；说明已检查范围、可验证的发现及其文件证据、未检查部分与下一步待查文件或目录。"
            "不能声称完成了整个项目审计、测试通过或执行了被阻止的调用。没有足够证据时明确说明。"
        )),
    ])
    note = f"本轮工具执行已达到上限（{rounds} 轮），以上为阶段性结果，剩余部分尚未检查。可发送“继续”接着处理。"
    if isinstance(response.content, str) and response.content.strip() and not response.tool_calls:
        content = f"{response.content.strip()}\n\n{note}"
    else:
        completed = sum(isinstance(item, ToolMessage) and item.status == "success" for item in messages)
        content = f"工具调用已达到本轮上限（{rounds} 轮）。已完成 {completed} 次工具调用，结果保留在运行记录中；尚未生成完整结论。可发送“继续”接着处理。"
    return {"messages": [*blocked, AIMessage(content=content)], "tool_limit_reached": True}
