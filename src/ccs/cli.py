"""命令行入口：argparse 定义和分发。"""

from __future__ import annotations

import argparse
import signal

from ccs.browser import process, tasks
from ccs.sandbox import lifecycle


def _add_exit_flags(p) -> None:
    p.add_argument("--exit", help="出口名（配置里的）或 socks5h://host:port")
    p.add_argument("--proxy", help="同 --exit 的地址写法（优先）")


def _add_create_commands(sub) -> None:
    p = sub.add_parser("new", help="建沙盒")
    p.add_argument("name")
    _add_exit_flags(p)
    p.set_defaults(func=lifecycle.cmd_new)


def _add_safety_flags(p) -> None:
    p.add_argument("--allow-ip", action="store_true", help="出口 IP 变了也允许启动")
    p.add_argument("--allow-region", action="store_true", help="出口在 Claude 不支持的地区也允许启动")


def _add_run_commands(sub) -> None:
    p = sub.add_parser("web", help="打开沙盒窗口，手工注册")
    p.add_argument("name")
    _add_exit_flags(p)
    p.add_argument("--timeout", type=int, default=0, help="多少秒后自动关闭（0 = 一直开着）")
    _add_safety_flags(p)
    p.set_defaults(func=tasks.cmd_web)

    p = sub.add_parser("export", help="导出沙盒身份（种子 + cookie + 指纹 pin）")
    p.add_argument("name")
    p.add_argument("-o", "--output", default="", help="输出文件（默认 <名字>.bundle.tar.gz）")
    p.set_defaults(func=lifecycle.cmd_export)

    p = sub.add_parser("import", help="从导出包新建沙盒（指纹和登录态一致）")
    p.add_argument("bundle")
    p.add_argument("name")
    p.set_defaults(func=lifecycle.cmd_import)

    p = sub.add_parser("refresh", help="探测会话是否还活着，并刷新账号信息")
    p.add_argument("name")
    _add_exit_flags(p)
    _add_safety_flags(p)
    p.set_defaults(func=tasks.cmd_refresh)

    p = sub.add_parser("check", help="无头自检：出口 IP / WebRTC（需要 xvfb）")
    p.add_argument("name")
    _add_exit_flags(p)
    _add_safety_flags(p)
    p.set_defaults(func=tasks.cmd_check)

    p = sub.add_parser("warmup", help="养号：发一句预设的话，等回复")
    p.add_argument("name")
    _add_exit_flags(p)
    p.add_argument("--prompt", default="", help="指定要说的话（默认随机挑一句）")
    p.add_argument("--headless", action="store_true", help="不显示窗口（需要 Xvfb）")
    _add_safety_flags(p)
    p.set_defaults(func=tasks.cmd_warmup)


def _add_manage_commands(sub) -> None:
    p = sub.add_parser("ls", aliases=["list"], help="列出沙盒")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=lifecycle.cmd_list)

    p = sub.add_parser("status", help="看一个沙盒")
    p.add_argument("name")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=lifecycle.cmd_status)

    p = sub.add_parser("set-exit", help="改沙盒的出口")
    p.add_argument("name")
    p.add_argument("exit", help="direct 或 socks5h://host:port")
    p.set_defaults(func=lifecycle.cmd_set_exit)

    p = sub.add_parser("key", aliases=["sessionkey"], help="复制沙盒的 sessionKey")
    p.add_argument("name")
    p.add_argument("--show", action="store_true", help="直接打印完整 key")
    p.set_defaults(func=lifecycle.cmd_key)

    p = sub.add_parser("mv", aliases=["rename"], help="给沙盒改名")
    p.add_argument("old")
    p.add_argument("new")
    p.set_defaults(func=lifecycle.cmd_mv)

    p = sub.add_parser("rm", aliases=["remove"], help="删除沙盒（含 profile）")
    p.add_argument("name")
    p.add_argument("--force", action="store_true", help="不确认，直接删")
    p.set_defaults(func=lifecycle.cmd_rm)


def _add_misc_commands(sub) -> None:
    p = sub.add_parser("log", help="看沙盒日志（会话输出 + 浏览器错误）")
    p.add_argument("name")
    p.add_argument("--lines", type=int, default=50)
    p.set_defaults(func=lifecycle.cmd_log)

    p = sub.add_parser("doctor", help="检查依赖和出口配置")
    p.set_defaults(func=lifecycle.cmd_doctor)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ccs", description="Claude 注册沙盒（手工/自动两用）")
    sub = parser.add_subparsers(dest="cmd", required=True)
    _add_create_commands(sub)
    _add_run_commands(sub)
    _add_manage_commands(sub)
    _add_misc_commands(sub)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    process.install_signal_handler()
    try:
        return args.func(args)
    except KeyboardInterrupt:
        process.handle_sigint(signal.SIGINT, None)
        return 130
