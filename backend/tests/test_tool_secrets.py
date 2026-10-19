import pytest

from app.security.redaction import redact_secrets
from app.security.tool_secrets import contains_secret


@pytest.mark.parametrize("value", [
    {"password": "plain-value"}, {"nested": [{"Authorization": "plain-value"}]},
    ["--password", "plain-value"], ["--api-key=plain-value"], "api_key=sk-abcdefghijklmnop",
])
def test_structured_secrets_never_become_approval_snapshots(value):
    assert contains_secret(value)


def test_normal_arguments_and_empty_credential_are_not_rejected():
    assert not contains_secret({"path": "src/main.py", "query": "ordinary", "token": ""})


def test_json_credentials_in_command_output_are_redacted():
    result = redact_secrets('output: {"password": "private-value", "token": "another-value"}')
    assert "private-value" not in result.text and "another-value" not in result.text
    assert result.redacted
