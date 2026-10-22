"""读 Firefox 的崩溃记录，告诉用户为什么崩。

浏览器崩溃时会写一个 .extra 文件到 ~/.config/mozilla/firefox/Crash Reports/pending，
里面是崩溃原因。最常见的是 Wayland 合成器断开：

    Error reading events from display: Broken pipe

这不是页面或 ccs 的问题，是 WSLg 的显示连接断了。

"""

from __future__ import annotations

import json
from pathlib import Path

REPORTS = Path.home() / ".config" / "mozilla" / "firefox" / "Crash Reports" / "pending"


def latest_reason(since: float) -> str:
    """取 since 之后最新的一条崩溃原因；没有就返回空串。"""
    try:
        files = list(REPORTS.glob("*.extra"))
    except OSError:
        return ""
    newest: tuple[float, Path] | None = None
    for file in files:
        try:
            modified = file.stat().st_mtime
        except OSError:
            continue
        if modified >= since and (newest is None or modified > newest[0]):
            newest = (modified, file)
    if newest is None:
        return ""
    try:
        data = json.loads(newest[1].read_text(encoding="utf-8", errors="ignore"))
    except (OSError, ValueError):
        return ""
    return str(data.get("MozCrashReason") or data.get("CrashReason") or "")


def explain(reason: str) -> str:
    """把常见原因翻成一句人话。"""
    if "display" in reason.lower() and "broken pipe" in reason.lower():
        return "WSLg 的显示连接断了（Wayland 合成器退出），不是账号或页面问题"
    return ""
