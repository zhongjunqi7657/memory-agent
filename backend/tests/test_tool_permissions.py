from app.permission.policy import PermissionPolicy


def test_permission_policy_requires_every_capability() -> None:
    decision = PermissionPolicy().decide(
        frozenset({"memory.read", "filesystem.read"}),
        frozenset({"memory.read"}),
    )

    assert decision.allowed is False
    assert decision.missing_permissions == frozenset({"filesystem.read"})
