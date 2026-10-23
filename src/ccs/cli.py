"""命令行入口：argparse 定义和分发。"""

from __future__ import annotations

import argparse
import signal
import sys

from ccs.browser import process, tasks
from ccs.sandbox import lifecycle

USAGE = """ccs — Claude 注册沙盒

最常用:
  ccs new <名字>                   建沙盒（出口自动探测本机代理）
  ccs web <名字>                   打开窗口，手工注册或日常对话
  ccs ls                           列出所有沙盒
  ccs status <名字>                看单个沙盒（含 sessionKey）

建号与迁移:
  ccs new <名字> [--exit 地址|direct]
  ccs import <包> <名字>           从导出包新建（指纹和登录态一致）
  ccs export <名字> [-o 文件]      导出身份包

启动:
  ccs web <名字>                   打开窗口，手工注册
  ccs warmup <名字> [--prompt …]   日常对话：发一句预设的话并等回复
  ccs refresh <名字>               探测会话是否还有效，刷新账号信息
  ccs check <名字>                 无头自检（出口 IP / WebRTC，需要 Xvfb）

管理:
  ccs set-exit <名字> <地址|direct>
  ccs key <名字>                   打印完整 sessionKey（同时尝试复制）
  ccs mv <旧名> <新名>  /  ccs rm <名字>
  ccs log <名字> [--lines 50]
  ccs doctor                       检查环境、出口和系统库

用法:
  ccs <命令> [参数…]               每个命令的详细参数：ccs <命令> -h

例子:
  ccs new 004
  ccs web 004
  ccs key 004
  ccs export 004 -o ~/004.bundle.tar.gz
"""


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

    p = sub.add_parser("warmup", help="日常对话：发一句预设的话，等回复")
    p.add_argument("name")
    _add_exit_flags(p)
    p.add_argument("--prompt", default="", help="指定要说的话（默认随机挑一句）")
    p.add_argument("--headless", action="store_true", help="不显示窗口（需要 Xvfb）")
    _add_safety_flags(p)
    p.set_defaults(func=tasks.cmd_warmup)


def _add_manage_commands(sub) -> None:
    p = sub.add_parser("ls", help="列出沙盒")
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

    p = sub.add_parser("key", help="打印沙盒的 sessionKey（完整）")
    p.add_argument("name")
    p.set_defaults(func=lifecycle.cmd_key)

    p = sub.add_parser("mv", help="给沙盒改名")
    p.add_argument("old")
    p.add_argument("new")
    p.set_defaults(func=lifecycle.cmd_mv)

    p = sub.add_parser("rm", help="删除沙盒（含 profile）")
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


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE, end="")  # noqa: T201 - 帮助就是打印
        return 0
    args = build_parser().parse_args(argv)
    process.install_signal_handler()
    try:
        return args.func(args)
    except KeyboardInterrupt:
        process.handle_sigint(signal.SIGINT, None)
        return 130
