"""命令入口：web / check / warmup / refresh。"""

from __future__ import annotations

from ccs.browser.session import run


def cmd_refresh(args) -> int:
    return run(args, mode="refresh")


def cmd_web(args) -> int:
    return run(args, mode="web")


def cmd_check(args) -> int:
    return run(args, mode="check")


def cmd_reg(args) -> int:
    return run(args, mode="reg")


def cmd_onboard(args) -> int:
    return run(args, mode="onboard")


def cmd_login(args) -> int:
    return run(args, mode="login")


def cmd_warmup(args) -> int:
    return run(args, mode="check" if getattr(args, "headless", False) else "warmup")
