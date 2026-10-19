from app.permission.policy import PermissionPolicy
from app.tools.request import request_permissions


def test_permission_policy_requires_every_capability() -> None:
    decision = PermissionPolicy().decide(
        frozenset({"memory.read", "filesystem.read"}),
        frozenset({"memory.read"}),
    )

    assert decision.allowed is False
    assert decision.missing_permissions == frozenset({"filesystem.read"})


def test_mcp_connection_configuration_is_not_a_user_execution_grant():
    class ConnectedManager:
        granted_permissions = frozenset({"filesystem.read", "filesystem.write", "external.side_effect"})

    assert request_permissions(ConnectedManager()) == frozenset({"memory.read", "memory.write.propose"})
