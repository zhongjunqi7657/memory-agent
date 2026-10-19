"""Explicit capability checks for tool execution."""

from dataclasses import dataclass

KNOWN_CAPABILITIES = frozenset(
    {
        "memory.read",
        "memory.write.propose",
        "filesystem.read",
        "filesystem.write",
        "network.access",
        "external.side_effect",
        "process.execute",
    }
)


@dataclass(frozen=True)
class PermissionDecision:
    allowed: bool
    missing_permissions: frozenset[str]
    reason: str


class PermissionPolicy:
    def decide(
        self,
        required_permissions: frozenset[str],
        granted_permissions: frozenset[str],
    ) -> PermissionDecision:
        missing = required_permissions - granted_permissions
        if missing:
            return PermissionDecision(
                allowed=False,
                missing_permissions=missing,
                reason="缺少工具执行所需权限",
            )
        return PermissionDecision(True, frozenset(), "权限满足")
