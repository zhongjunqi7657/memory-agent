"""Aggregate provider chunks while publishing only visible answer text."""

from collections.abc import Awaitable, Callable, Sequence
from time import monotonic
from uuid import uuid4

from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    message_chunk_to_message,
)

OutputSink = Callable[[str, dict[str, object]], Awaitable[None]]

# Transport batching: avoid a database commit and UI render for every token.
OUTPUT_INTERVAL_SECONDS = 0.1
OUTPUT_BATCH_CHARACTERS = 256


def _message(chunks: AIMessage | AIMessageChunk | None) -> AIMessage:
    if chunks is None:
        raise ValueError("模型未返回任何输出")
    result = message_chunk_to_message(chunks)
    if not isinstance(result, AIMessage):
        raise TypeError("模型返回的消息类型无效")
    return result


def stream_model_sync(model, messages: Sequence[BaseMessage]) -> AIMessage:
    combined = None
    for chunk in model.stream(messages):
        combined = chunk if combined is None else combined + chunk
    return _message(combined)


async def stream_model(
    model, messages: Sequence[BaseMessage], *, emit: OutputSink | None = None,
) -> AIMessage:
    output_id = str(uuid4())
    if emit:
        await emit("model.output.started", {"output_id": output_id})
    combined = None
    pending = ""
    last_delivery = None
    stream = model.astream(messages)
    try:
        async for chunk in stream:
            combined = chunk if combined is None else combined + chunk
            # .text excludes reasoning blocks, tool arguments and provider metadata.
            pending += chunk.text
            now = monotonic()
            if emit and pending and (
                last_delivery is None or now - last_delivery >= OUTPUT_INTERVAL_SECONDS
                or len(pending) >= OUTPUT_BATCH_CHARACTERS
            ):
                await emit("model.output.delta", {"output_id": output_id, "text": pending})
                pending = ""
                last_delivery = now
    finally:
        # Explicitly close the upstream stream on errors and task cancellation.
        await stream.aclose()
    if emit and pending:
        await emit("model.output.delta", {"output_id": output_id, "text": pending})
    return _message(combined)
