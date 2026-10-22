"""进程：找浏览器进程、判断僵尸、发信号、清 profile 锁、Ctrl-C 收摊。"""

from __future__ import annotations

import os
import signal
import subprocess
import time
from contextlib import suppress
from pathlib import Path

from ccs import errors

_DEFAULT_TIMEOUTS = {
    "pgrep_s": 5,
    "close_grace_s": 5,
    "kill_poll_s": 0.2,
    "kill_settle_s": 0.3,
}
_TIMEOUTS = dict(_DEFAULT_TIMEOUTS)
_EXIT_HOOKS: list = []


def configure(timeouts: dict | None = None) -> None:
    _TIMEOUTS.update(timeouts or {})


def clear_hooks() -> None:
    _EXIT_HOOKS.clear()


def add_hook(hook) -> None:
    _EXIT_HOOKS.append(hook)


def handle_sigint(signum, frame) -> None:          # noqa: ARG001 - 签名固定
    """Ctrl-C 立刻收摊。

    Playwright 的同步接口会把 KeyboardInterrupt 吞掉，让进程卡在关闭上
    （实测 138 秒）。所以这里不走异常，直接做完清理再硬退出。
    """
    for hook in _EXIT_HOOKS:
        with suppress(Exception):
            hook()
    errors.log_line("收到 Ctrl-C，退出")
    print("\n已退出（沙盒数据已保留）", flush=True)  # noqa: T201 - Ctrl-C 退出提示直接打印
    os._exit(130)


def install_signal_handler() -> None:
    signal.signal(signal.SIGINT, handle_sigint)


def profile_pids(profile: Path) -> list[int]:
    try:
        # 用 profile 目录匹配：浏览器（-profile 参数）和崩溃报告器（dmp 路径）
        # 都带这个路径，一次都能找到。-- 是因为路径里可能有以 - 开头的片段。
        out = subprocess.run(  # noqa: S603 - pgrep 是固定命令，参数是我们拼的，没有 shell
            ["pgrep", "-f", "--", str(profile)],  # noqa: S607 - 用 PATH 里的 pgrep，命令固定
            capture_output=True, text=True, check=False,
            timeout=_TIMEOUTS["pgrep_s"])
    except (OSError, subprocess.TimeoutExpired):
        return []
    return [int(x) for x in out.stdout.split() if x.strip().isdigit()]


def stop_browser(profile: Path, grace: float | None = None) -> None:
    """结束这个 profile 的浏览器：先 SIGTERM，等不到就 SIGKILL。"""
    if grace is None:
        grace = float(_TIMEOUTS["close_grace_s"])
    pids = profile_pids(profile)
    if not pids:
        return
    for pid in pids:
        with suppress(OSError):
            os.kill(pid, signal.SIGTERM)
    deadline = time.time() + grace
    while time.time() < deadline:
        if not profile_pids(profile):
            break
        time.sleep(_TIMEOUTS["kill_poll_s"])
    for pid in profile_pids(profile):
        with suppress(OSError):
            os.kill(pid, signal.SIGKILL)
    time.sleep(_TIMEOUTS["kill_settle_s"])


def browser_pids(profile: Path) -> list[int]:
    """浏览器主进程（不含崩溃报告器）。"""
    out = []
    for pid in profile_pids(profile):
        try:
            cmd = Path(f"/proc/{pid}/cmdline").read_bytes().replace(b"\0", b" ").decode(
                "utf-8", "replace")
        except OSError:
            continue
        if "firefox" in cmd and "-profile" in cmd:
            out.append(pid)
    return out


def pid_alive(pid: int) -> bool:
    """活着且不是僵尸。

    os.kill(pid, 0) 对僵尸进程也返回成功，浏览器崩了以后会一直是僵尸，
    所以必须看 /proc 里的状态位。
    """
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    try:
        return stat.rsplit(")", 1)[1].split()[0] != "Z"
    except IndexError:
        return False


def clear_lock_files(profile: Path) -> None:
    """删掉 Firefox 的 profile 锁。进程已经不在时才删。"""
    for name in ("lock", ".parentlock"):
        path = profile / name
        try:
            if os.path.lexists(path):
                path.unlink()
                errors.log_line(f"清掉锁文件: {path}")
        except OSError as exc:
            errors.log_line(f"清锁文件失败 {path}: {exc}")


def shutdown_browser(profile: Path, grace: float | None = None) -> None:
    """收摊：结束浏览器，再清锁。Ctrl-C 和关窗口都走这里。"""
    stop_browser(profile, grace=grace)
    clear_lock_files(profile)


def prepare_profile(profile: Path, grace: float | None = None) -> None:
    """启动前兜底。

    正常退出时下面已经清过；这里只处理"上次被 kill -9"这种没机会收摊的情况。
    """
    pids = profile_pids(profile)
    if pids:
        errors.say(f"上一个浏览器还在跑（PID {', '.join(map(str, pids))}），先结束它")
    shutdown_browser(profile, grace=grace)
