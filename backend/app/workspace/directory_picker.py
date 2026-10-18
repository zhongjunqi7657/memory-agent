"""Run a user-operated directory dialog outside the API event loop."""

import json
import subprocess
import sys
import threading
from pathlib import Path

_dialog_lock = threading.Lock()


class DirectoryPickerError(ValueError):
    pass


def choose_directory() -> str | None:
    if sys.platform != "win32":
        raise DirectoryPickerError("当前系统不支持本地选择窗口，请手动填写目录")
    if not _dialog_lock.acquire(blocking=False):
        raise DirectoryPickerError("已有文件夹选择窗口打开，请先完成或取消选择")
    try:
        result = subprocess.run(
            [sys.executable, "-I", str(Path(__file__).resolve())],
            capture_output=True, text=True, encoding="utf-8", timeout=180, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        if result.returncode != 0:
            raise DirectoryPickerError("无法打开文件夹选择窗口，请手动填写目录")
        return json.loads(result.stdout)
    except subprocess.TimeoutExpired as error:
        raise DirectoryPickerError("选择窗口已超时，请重新选择") from error
    except (OSError, ValueError) as error:
        if isinstance(error, DirectoryPickerError):
            raise
        raise DirectoryPickerError("无法读取选择结果，请手动填写目录") from error
    finally:
        _dialog_lock.release()


if __name__ == "__main__":
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        directory = filedialog.askdirectory(parent=root, title="选择项目文件夹", mustexist=True)
        print(json.dumps(directory or None, ensure_ascii=True))
    finally:
        root.destroy()
