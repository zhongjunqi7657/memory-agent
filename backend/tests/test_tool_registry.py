from dataclasses import dataclass
from typing import Any

import pytest

from app.protocol.tools import ToolExecutionContext, ToolResult
from app.tools.registry import ToolRegistry


@dataclass
class FakeTool:
    id: str = "builtin.test.echo"
    model_name: str = "builtin__test__echo"
    remote_name: str | None = None
    description: str = "Echo"
    input_schema: dict[str, Any] = None  # type: ignore[assignment]
    required_permissions: frozenset[str] = frozenset()
    timeout_seconds: float = 1.0

    def __post_init__(self) -> None:
        if self.input_schema is None:
            self.input_schema = {"type": "object"}

    async def execute(self, arguments, context: ToolExecutionContext) -> ToolResult:
        return ToolResult.success(dict(arguments))


def test_registry_maps_internal_and_model_names() -> None:
    tool = FakeTool()
    registry = ToolRegistry([tool])

    assert registry.get(tool.id) is tool
    assert registry.get(tool.model_name) is tool
    assert registry.function_schemas()[0]["function"]["parameters"] == {
        "type": "object"
    }


def test_registry_rejects_name_conflicts_and_invalid_schema() -> None:
    registry = ToolRegistry([FakeTool()])
    with pytest.raises(ValueError, match="工具 ID 重复"):
        registry.register(FakeTool(model_name="another_name"))
    with pytest.raises(ValueError, match="Schema"):
        ToolRegistry([FakeTool(input_schema={"type": "not-a-json-type"})])


def test_registry_preserves_local_refs_and_rejects_external_schema():
    tool = FakeTool(input_schema={"type": "object", "$defs": {"value": {"type": "integer"}},
        "properties": {"value": {"$ref": "#/$defs/value"}}})
    registry = ToolRegistry([tool])
    schema = registry.function_schemas()[0]["function"]["parameters"]
    assert schema == tool.input_schema
    schema["$defs"]["value"]["type"] = "string"
    assert tool.input_schema["$defs"]["value"]["type"] == "integer"
    with pytest.raises(ValueError, match="外部引用"):
        ToolRegistry([FakeTool(input_schema={"$ref": "https://example.com/schema.json"})])
    with pytest.raises(ValueError, match="方言"):
        ToolRegistry([FakeTool(input_schema={"$schema": "https://invalid.example/schema"})])


def test_hidden_tools_are_not_exposed_to_model_but_remain_identifiable():
    tool = FakeTool()
    registry = ToolRegistry([tool], enabled_tool_ids=frozenset())
    assert registry.snapshot() == ()
    assert registry.function_schemas() == []
    assert registry.get(tool.id) is tool
