import json
import subprocess
import sys

from app.cli.main import main


def test_cli_lists_schemas_permissions_without_model_or_database(capsys):
    assert main(["tools", "list"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert len(result["tools"]) == 3
    assert result["tools"][0]["inputSchema"]["properties"]["query"]["type"] == "string"
    assert result["tools"][2]["permissions"] == ["memory.write.propose"]


def test_cli_entrypoint_does_not_import_provider_module():
    result = subprocess.run([sys.executable, "-c",
        ("import sys; from app.cli.main import main; main(['tools', 'list']); "
         "assert 'app.models.qwen' not in sys.modules")], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
