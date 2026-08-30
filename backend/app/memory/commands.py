"""Deterministic recognition of explicit user memory commands."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class MemoryCommandType(str, Enum):
    SAVE = "save"
    FORGET = "forget"
    CORRECT = "correct"
    LIST = "list"


@dataclass(frozen=True)
class MemoryCommand:
    type: MemoryCommandType
    target: str
    replacement: str | None = None


_SAVE = re.compile(
    r"^\s*(?:请(?:帮我)?\s*)?(?:记住|记下|记录)\s*(?:我\s*)?(?P<target>.+?)\s*[。.!！]?\s*$"
)
_FORGET = re.compile(
    r"^\s*(?:请(?:帮我)?\s*)?(?:忘记|忘掉|删除)\s*(?:我\s*)?(?P<target>.+?)\s*[。.!！]?\s*$"
)
_CORRECT = re.compile(
    r"^\s*(?:请\s*)?把\s*(?P<target>.+?)\s*(?:改成|更正为|更新为)\s*(?P<replacement>.+?)\s*[。.!！]?\s*$"
)
_LIST = re.compile(
    r"^\s*(?:你(?:还)?记住了我什么|你记得我什么|查看(?:我的)?记忆|列出(?:我的)?记忆)\s*[？?。.!！]?\s*$"
)


def parse_memory_command(content: str) -> MemoryCommand | None:
    """Recognize imperative commands without classifying ordinary conversation."""

    if _LIST.match(content):
        return MemoryCommand(type=MemoryCommandType.LIST, target="")
    for pattern, command_type in (
        (_SAVE, MemoryCommandType.SAVE),
        (_FORGET, MemoryCommandType.FORGET),
    ):
        match = pattern.match(content)
        if match:
            target = match.group("target").strip()
            return MemoryCommand(type=command_type, target=target) if target else None
    match = _CORRECT.match(content)
    if match:
        target = match.group("target").strip()
        replacement = match.group("replacement").strip()
        if target and replacement:
            return MemoryCommand(
                type=MemoryCommandType.CORRECT,
                target=target,
                replacement=replacement,
            )
    return None
