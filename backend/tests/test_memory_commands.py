from app.memory.commands import MemoryCommandType, parse_memory_command


def test_parse_explicit_save_command() -> None:
    command = parse_memory_command("请记住我喜欢先理解原理再看代码")

    assert command is not None
    assert command.type is MemoryCommandType.SAVE
    assert command.target == "喜欢先理解原理再看代码"


def test_parse_forget_command_without_matching_ordinary_statement() -> None:
    command = parse_memory_command("忘记我之前说的考研计划。")

    assert command is not None
    assert command.type is MemoryCommandType.FORGET
    assert command.target == "之前说的考研计划"
    assert parse_memory_command("我忘记了昨天的事情") is None


def test_parse_correction_command() -> None:
    command = parse_memory_command("把我的目标改成参加秋招")

    assert command is not None
    assert command.type is MemoryCommandType.CORRECT
    assert command.target == "我的目标"
    assert command.replacement == "参加秋招"


def test_parse_memory_list_command() -> None:
    command = parse_memory_command("你记住了我什么？")

    assert command is not None
    assert command.type is MemoryCommandType.LIST
    assert command.target == ""
