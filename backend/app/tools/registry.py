"""Registration, lookup and model discovery for unified tools."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from copy import deepcopy
from typing import Any

from jsonschema.exceptions import SchemaError
from jsonschema.validators import validator_for

from app.tools.base import Tool


class ToolRegistry:
    def __init__(self, tools: Iterable[Tool] = (), *, enabled: bool = True,
                 enabled_tool_ids: frozenset[str] | None = None) -> None:
        self.enabled = enabled
        self._enabled_tool_ids = enabled_tool_ids
        self._by_id: dict[str, Tool] = {}
        self._by_model_name: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if not tool.id or not tool.model_name:
            raise ValueError("工具 ID 和模型名称不能为空")
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", tool.model_name):
            raise ValueError("工具模型名称不符合 Function Calling 协议")
        if tool.id in self._by_id or tool.id in self._by_model_name:
            raise ValueError(f"工具 ID 重复: {tool.id}")
        if tool.model_name in self._by_model_name or tool.model_name in self._by_id:
            raise ValueError(f"工具模型名称重复: {tool.model_name}")
        if not math.isfinite(tool.timeout_seconds) or tool.timeout_seconds <= 0:
            raise ValueError(f"工具超时必须大于 0: {tool.id}")
        schema = dict(tool.input_schema)
        if schema.get("type") not in {None, "object"}:
            raise ValueError(f"工具输入 Schema 必须接受对象参数: {tool.id}")
        try:
            validator = validator_for(schema, default=None) if "$schema" in schema else validator_for(schema)
            if validator is None:
                raise ValueError("不支持的工具 JSON Schema 方言")
            validator.check_schema(schema)
            self._check_local_references(schema)
        except SchemaError as error:
            raise ValueError(f"工具输入 Schema 非法: {tool.id}") from error
        self._by_id[tool.id] = tool
        self._by_model_name[tool.model_name] = tool

    @staticmethod
    def _check_local_references(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in {"$ref", "$dynamicRef"} and not str(child).startswith("#"):
                    raise ValueError("工具 Schema 不允许外部引用")
                ToolRegistry._check_local_references(child)
        elif isinstance(value, list):
            for child in value:
                ToolRegistry._check_local_references(child)

    def get(self, name: str) -> Tool | None:
        return self._by_id.get(name) or self._by_model_name.get(name)

    def snapshot(self) -> tuple[Tool, ...]:
        return tuple(tool for tool in self._by_id.values() if self.is_enabled(tool))

    def is_enabled(self, tool: Tool) -> bool:
        return self.enabled and (self._enabled_tool_ids is None or tool.id in self._enabled_tool_ids)

    def function_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.model_name,
                    "description": tool.description,
                    "parameters": deepcopy(dict(tool.input_schema)),
                },
            }
            for tool in self.snapshot()
        ]
