"""输出和退出：统一写控制台和沙盒日志。

日志文件由 run() 通过 set_log_path() 设成 ~/sandboxes/<名字>/ccs.log。
"""

from __future__ import annotations

import sys
import time
from typing import TYPE_CHECKING, NoReturn

if TYPE_CHECKING:
    from pathlib import Path

# 进程级单例：日志路径在启动时设一次，之后所有模块共用。
LOG_PATH: Path | None = None


def set_log_path(path: Path | None) -> None:
    global LOG_PATH  # noqa: PLW0603 - 进程级日志路径，启动时设置一次
    LOG_PATH = path


def log_line(msg: str) -> None:
    if LOG_PATH is None:
        return
    try:
        with LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except OSError:
        pass


def die(msg: str) -> NoReturn:
    print(f"错误: {msg}", file=sys.stderr)  # noqa: T201 - 命令行错误输出就是打印
    log_line(f"错误: {msg}")
    raise SystemExit(1)


def say(msg: str = "") -> None:
    print(msg, flush=True)  # noqa: T201 - 命令行正常输出就是打印
    log_line(msg)
