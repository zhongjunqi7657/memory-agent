from pathlib import Path

from langchain_core.language_models.fake_chat_models import FakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agent.graph import build_graph
from app.config.business import BusinessConfig


class FixedChatModel(FakeChatModel):
    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(content="收到，我会记住你的学习目标。")
                )
            ]
        )


def test_business_config_loads_toml(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[models]\nchat_model = "qwen-turbo"\nembedding_dimensions = 1024\n',
        encoding="utf-8",
    )
    config = BusinessConfig.load(config_path)
    assert config.models.chat_model == "qwen-turbo"
    assert config.models.embedding_dimensions == 1024


def test_graph_returns_model_message() -> None:
    graph = build_graph(FixedChatModel())
    result = graph.invoke({"messages": [HumanMessage(content="我想学 Python")]})
    assert result["messages"][-1].content == "收到，我会记住你的学习目标。"
