"""命令行入口：argparse 定义和分发。"""

from __future__ import annotations

import argparse
import signal
import sys

from ccs import ui
from ccs.browser import process, tasks
from ccs.cloud import commands as cloud_commands
from ccs.sandbox import commands

INTRO = "ccs — Claude 注册沙盒：一个号一个沙盒，指纹、登录态、出口都分开。"

HELP_SECTIONS = [
    ("常用", [
        ("new <名字> [--exit 地址|direct]", "建沙盒（出口自动探测本机代理）"),
        ("web <名字>", "打开窗口，手工注册或日常对话"),
        ("reg <名字> [--email 邮箱]", "半自动注册（新号首登）"),
        ("login <名字>", "重新登录（只用已登记的邮箱）"),
        ("onboard <名字>", "只走 onboarding（引导卡住时用）"),
        ("ls", "列出所有沙盒"),
        ("status <名字>", "看单个沙盒（含 sessionKey）"),
    ]),
    ("日常", [
        ("warmup <名字> [--prompt 话]", "发一句预设的话并等回复"),
        ("refresh <名字>", "探测会话是否还有效，刷新账号信息"),
        ("check <名字>", "无头自检（出口 IP / WebRTC，需要 Xvfb）"),
    ]),
    ("云端（多台机器、多人共用同一个号池）", [
        ("cloud login <地址>", "登录同步服务（存 token）"),
        ("cloud ls", "看云端有哪些号"),
        ("cloud push [名字…]", "上传；不写名字就推全部"),
        ("cloud pull [名字|邮箱…]", "下载最新版；不写就是全部"),
        ("cloud log <名字|邮箱>", "看版本历史"),
        ("cloud revert <名字> --to 5", "把第 5 版的内容推成新版本"),
        ("cloud user add <名字>", "建人（只有管理员）"),
    ]),
    ("迁移", [
        ("export <名字> [-o 文件]", "导出身份包"),
        ("import <包> [名字]", "从导出包新建（不写名字就用包里的）"),
    ]),
    ("管理", [
        ("set proxy <名字> <地址|direct>", "改这个沙盒的出口"),
        ("key <名字>", "打印完整 sessionKey（同时尝试复制）"),
        ("mv <旧名> <新名>", "给沙盒改名"),
        ("rm <名字> [--force]", "删除沙盒（含 profile）"),
        ("log <名字> [--lines 50]", "看沙盒日志"),
        ("doctor", "检查环境、出口和系统库"),
    ]),
]

FOOTER = """用法:
  ccs <命令> [参数…]      每个命令的详细参数：ccs <命令> -h

例子:
  ccs new 004
  ccs web 004
  ccs key 004
  ccs export 004 -o ~/004.bundle.tar.gz
  ccs cloud push 004"""


def print_help() -> None:
    ui.emit_sections(HELP_SECTIONS, intro=INTRO, footer=FOOTER)


class _Formatter(argparse.RawDescriptionHelpFormatter):
    """命令行帮助排版：标题中文、说明对齐到 32 列。"""

    def __init__(self, prog: str):
        super().__init__(prog, max_help_position=32, width=100)

    def _format_usage(self, usage, actions, groups, prefix):  # noqa: ARG002 - 签名照抄 argparse
        return super()._format_usage(usage, actions, groups, prefix="用法: ")


def _add_exit_flags(p) -> None:
    p.add_argument("--exit", help="出口名（配置里的）或 socks5h://host:port")
    p.add_argument("--proxy", help="同 --exit 的地址写法（优先）")


def _add_create_commands(sub) -> None:
    p = sub.add_parser("new", help="建沙盒")
    p.add_argument("name")
    _add_exit_flags(p)
    p.set_defaults(func=commands.cmd_new)


def _add_record_flag(p) -> None:
    p.add_argument("--record", action="store_true",
                   help="录制点击/输入和截图，存到沙盒的 record/ 目录")


def _add_safety_flags(p) -> None:
    p.add_argument("--allow-ip", action="store_true", help="出口 IP 变了也允许启动")
    p.add_argument("--allow-region", action="store_true", help="出口在 Claude 不支持的地区也允许启动")


def _add_run_commands(sub) -> None:  # noqa: PLR0915 - 命令注册就是一堆参数
    p = sub.add_parser("web", help="打开沙盒窗口，手工注册")
    p.add_argument("name")
    _add_exit_flags(p)
    p.add_argument("--timeout", type=int, default=0, help="多少秒后自动关闭（0 = 一直开着）")
    _add_record_flag(p)
    _add_safety_flags(p)
    p.set_defaults(func=tasks.cmd_web)

    p = sub.add_parser("reg", help="半自动注册/重新登录：自动填表、取信里的登录链接")
    p.add_argument("name")
    p.add_argument("--email", default="", help="指定邮箱；不写就按沙盒名生成哈希地址")
    _add_record_flag(p)
    _add_exit_flags(p)
    _add_safety_flags(p)
    p.set_defaults(func=tasks.cmd_reg)

    p = sub.add_parser("onboard", help="只走 onboarding（已登录、但引导没走完）")
    p.add_argument("name")
    _add_exit_flags(p)
    _add_record_flag(p)
    _add_safety_flags(p)
    p.set_defaults(func=tasks.cmd_onboard)

    p = sub.add_parser("login", help="重新登录：只用已登记的邮箱，不新建地址")
    p.add_argument("name")
    p.add_argument("--email", default="", help="覆盖已登记的邮箱（一般不用）")
    _add_record_flag(p)
    _add_exit_flags(p)
    _add_safety_flags(p)
    p.set_defaults(func=tasks.cmd_login)

    p = sub.add_parser("export", help="导出沙盒身份（种子 + cookie + 指纹 pin）")
    p.add_argument("name")
    p.add_argument("-o", "--output", default="", help="输出文件（默认 <名字>.bundle.tar.gz）")
    p.set_defaults(func=commands.cmd_export)

    p = sub.add_parser("import", help="从导出包新建沙盒（指纹和登录态一致）")
    p.add_argument("bundle")
    p.add_argument("name", nargs="?", default="", help="不写就用包里的原名字")
    p.add_argument("--force", action="store_true", help="同名就覆盖（默认自动加 -2）")
    p.set_defaults(func=commands.cmd_import)

    p = sub.add_parser("refresh", help="探测会话是否还活着，并刷新账号信息")
    p.add_argument("name")
    _add_exit_flags(p)
    _add_record_flag(p)
    _add_safety_flags(p)
    p.set_defaults(func=tasks.cmd_refresh)

    p = sub.add_parser("check", help="无头自检：出口 IP / WebRTC（需要 xvfb）")
    p.add_argument("name")
    _add_exit_flags(p)
    _add_record_flag(p)
    _add_safety_flags(p)
    p.set_defaults(func=tasks.cmd_check)

    p = sub.add_parser("warmup", help="日常对话：发一句预设的话，等回复")
    p.add_argument("name")
    _add_exit_flags(p)
    p.add_argument("--prompt", default="", help="指定要说的话（默认随机挑一句）")
    p.add_argument("--headless", action="store_true", help="不显示窗口（需要 Xvfb）")
    _add_record_flag(p)
    _add_safety_flags(p)
    p.set_defaults(func=tasks.cmd_warmup)


def _add_manage_commands(sub) -> None:
    p = sub.add_parser("ls", help="列出沙盒")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=commands.cmd_list)

    p = sub.add_parser("status", help="看一个沙盒")
    p.add_argument("name")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=commands.cmd_status)

    p = sub.add_parser("set", help="修改沙盒设置")
    set_sub = p.add_subparsers(dest="set_command", required=True)
    q = set_sub.add_parser("proxy", help="改沙盒的出口")
    q.add_argument("name")
    q.add_argument("exit", help="direct 或 socks5h://host:port")
    q.set_defaults(func=commands.cmd_set_proxy)

    p = sub.add_parser("key", help="打印沙盒的 sessionKey（完整）")
    p.add_argument("name")
    p.set_defaults(func=commands.cmd_key)

    p = sub.add_parser("mv", help="给沙盒改名")
    p.add_argument("old")
    p.add_argument("new")
    p.set_defaults(func=commands.cmd_mv)

    p = sub.add_parser("rm", help="删除沙盒（含 profile）")
    p.add_argument("name")
    p.add_argument("--force", action="store_true", help="不确认，直接删")
    p.set_defaults(func=commands.cmd_rm)


def _add_misc_commands(sub) -> None:
    p = sub.add_parser("log", help="看沙盒日志（会话输出 + 浏览器错误）")
    p.add_argument("name")
    p.add_argument("--lines", type=int, default=50)
    p.set_defaults(func=commands.cmd_log)

    p = sub.add_parser("doctor", help="检查依赖和出口配置")
    p.set_defaults(func=commands.cmd_doctor)


def _add_cloud_commands(sub) -> None:
    p = sub.add_parser("cloud", help="云端同步：按版本存账号，多人共用")
    cloud = p.add_subparsers(dest="cloud_command", required=True)

    q = cloud.add_parser("login", help="登录同步服务（存 token）")
    q.add_argument("url", nargs="?", default="", help="服务地址，例如 https://sync.example.com")
    q.add_argument("--token", default="", help="直接给 token（不写就问）")
    q.set_defaults(func=cloud_commands.cmd_cloud_login)

    q = cloud.add_parser("whoami", help="看当前登录的是谁")
    q.set_defaults(func=cloud_commands.cmd_cloud_whoami)

    q = cloud.add_parser("ls", help="列出云端的账号")
    q.set_defaults(func=cloud_commands.cmd_cloud_ls)

    q = cloud.add_parser("push", help="上传（不写名字就推全部沙盒）")
    q.add_argument("names", nargs="*", help="沙盒名；不写就是全部")
    q.add_argument("-m", "--message", default="", help="这一版的说明")
    q.add_argument("--force", action="store_true", help="和云端不一致也推成新版本")
    q.set_defaults(func=cloud_commands.cmd_cloud_push)

    q = cloud.add_parser("pull", help="下载最新版（可写沙盒名或邮箱）")
    q.add_argument("refs", nargs="*", help="沙盒名或邮箱；不写就是云端全部")
    q.add_argument("--as", dest="name", default="", help="拉成本地的这个名字")
    q.add_argument("--force", action="store_true", help="本地有改动也覆盖")
    q.set_defaults(func=cloud_commands.cmd_cloud_pull)

    q = cloud.add_parser("log", help="看一个账号的版本历史")
    q.add_argument("ref", help="沙盒名或邮箱")
    q.set_defaults(func=cloud_commands.cmd_cloud_log)

    q = cloud.add_parser("revert", help="把某一版的内容推成新版本（不算覆盖）")
    q.add_argument("ref", help="沙盒名或邮箱")
    q.add_argument("--to", type=int, required=True, help="回到哪一版，例如 5")
    q.set_defaults(func=cloud_commands.cmd_cloud_revert)

    q = cloud.add_parser("user", help="建人（只有管理员）")
    user = q.add_subparsers(dest="user_command", required=True)
    a = user.add_parser("add", help="建一个用户，打印一次 token")
    a.add_argument("name")
    a.set_defaults(func=cloud_commands.cmd_cloud_user)


def _polish(parser: argparse.ArgumentParser) -> None:
    """把 argparse 的英文标题换成中文，并递归处理子命令。"""
    parser.formatter_class = _Formatter
    parser._positionals.title = "位置参数"   # noqa: SLF001 - argparse 没有公开开关
    parser._optionals.title = "选项"         # noqa: SLF001 - 同上
    for action in parser._actions:           # noqa: SLF001 - 同上
        if isinstance(action, argparse._HelpAction):  # noqa: SLF001 - 同上
            action.help = "显示帮助并退出"
        if isinstance(action, argparse._SubParsersAction):  # noqa: SLF001 - 同上
            for child in action.choices.values():
                _polish(child)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ccs",
                                     description="Claude 注册沙盒（手工/自动两用）")
    sub = parser.add_subparsers(dest="cmd", required=True)
    _add_create_commands(sub)
    _add_run_commands(sub)
    _add_manage_commands(sub)
    _add_misc_commands(sub)
    _add_cloud_commands(sub)
    _polish(parser)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print_help()
        return 0
    args = build_parser().parse_args(argv)
    process.install_signal_handler()
    try:
        return args.func(args)
    except KeyboardInterrupt:
        process.handle_sigint(signal.SIGINT, None)
        return 130
