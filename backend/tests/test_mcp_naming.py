import pytest

from app.mcp.naming import tool_names


def test_three_names_keep_remote_name_independent():
    assert tool_names("filesystem", "read_file") == (
        "mcp.filesystem.read_file", "mcp__filesystem__read_file"
    )


@pytest.mark.parametrize("server,remote", [("bad.id", "read"), ("ok", "bad/name"),
                                            ("bad__id", "read"), ("ok", "x" * 80)])
def test_naming_rejects_invalid_or_ambiguous_names(server, remote):
    with pytest.raises(ValueError):
        tool_names(server, remote)
