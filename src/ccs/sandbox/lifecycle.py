"""沙盒生命周期命令：new / ls / status / mv / rm，以及按名字看信息的命令。"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import time
from contextlib import suppress
from pathlib import Path

from ccs import config, paths, report, ui
from ccs.browser import process
from ccs.browser.prefs import build_pin
from ccs.errors import die, log_line, say
from ccs.network import pick_exit, resolve_exit
from ccs.sandbox import bundle, store
from ccs.sandbox.model import Sandbox
from ccs.ui import rich_version


def load_runtime() -> dict:
    """读配置，并把路径、进程的模块参数配好。"""
    cfg = config.load_config()
    paths.configure(cfg, config.source_path())
    process.configure(config_timeouts(cfg))
    return cfg


def config_timeouts(cfg: dict) -> dict:
    return (cfg.get("browser") or {}).get("timeouts") or {}


def _check_name(name: str) -> None:
    if name in {".", ".."} or "/" in name or "\\" in name:
        die("沙盒名字不能带路径分隔符")


def cmd_new(args) -> int:
    cfg = load_runtime()
    _check_name(args.name)
    spec, _proxy = resolve_exit(pick_exit(args), cfg)
    d = paths.sandbox_dir(args.name)
    if d.exists():
        die(f"{d} 已存在")
    d.mkdir(parents=True)
    meta = Sandbox(name=args.name, exit=spec,
                   created_at=time.strftime("%Y-%m-%d %H:%M:%S"))
    store.write_meta(args.name, meta)
    profile = d / paths.PROFILE_DIR_NAME
    profile.mkdir()
    seed, created = store.seed_of(profile)
    if created:
        say(f"指纹种子: {seed}（写入 seed.json，这个沙盒以后一直用它）")
    say(f"已建沙盒 {args.name}（出口 {spec}，目录 {d}）")
    say(f"下一步: ccs web {args.name}")
    return 0


def _rows() -> list[dict]:
    rows = []
    for sandbox in store.list_sandboxes():
        row = sandbox.to_meta()
        row["account"] = store.read_account(sandbox.name)
        rows.append(row)
    return rows


def cmd_list(args) -> int:
    load_runtime()
    rows = _rows()
    if getattr(args, "json", False):
        print(json.dumps({"root": str(paths.root()), "slots": rows},  # noqa: T201 - --json 输出就是打印
                         ensure_ascii=False, indent=2))
        return 0
    if not rows:
        say("还没有沙盒（ccs new <名字>）")
        return 0
    headers, cells = report.ls_rows(rows)
    fallback = report.ls_lines(rows)
    for line in fallback:
        log_line(line)
    ui.emit_table(headers, cells, fallback=fallback, styles=report.ls_styles(cells))
    return 0


def copy_to_clipboard(text: str) -> bool:
    """复制到 Windows 剪贴板（WSL 里用 clip.exe）。"""
    try:
        subprocess.run(["clip.exe"], input=text.encode("utf-16-le"), check=True, timeout=5)  # noqa: S607 - clip.exe 是固定命令，没有 shell
    except (OSError, subprocess.SubprocessError):
        return False
    else:
        return True


def cmd_key(args) -> int:
    load_runtime()
    acct = store.read_account(args.name)
    key = acct.get("session_key") or ""
    if not key:
        die(f"没有 session_key：先 ccs web {args.name} 登录一次，抓到了才有")
    for line in report.account_lines(acct):
        say(line)
    if getattr(args, "show", False):
        print(key)  # noqa: T201 - --show 就是直接打印 key
    else:
        say(f"sessionKey: {key[:18]}…（共 {len(key)} 字符）")
    if copy_to_clipboard(key):
        say("已复制到剪贴板")
    else:
        say("复制失败（没有 clip.exe），加 --show 直接打印")
    return 0


def _profile_size(profile) -> int:
    size = 0
    for path in profile.rglob("*") if profile.is_dir() else []:
        with suppress(OSError):
            if path.is_file():
                size += path.stat().st_size
    return size


def _read_seed(profile) -> int | None:
    seed_file = profile / "seed.json"
    if not seed_file.is_file():
        return None
    try:
        return json.loads(seed_file.read_text(encoding="utf-8")).get("seed")
    except (OSError, json.JSONDecodeError):
        return None


def cmd_status(args) -> int:
    load_runtime()
    meta = store.read_meta(args.name)
    d = paths.sandbox_dir(args.name)
    profile = paths.profile_dir(args.name)
    info = {**meta, "dir": str(d), "seed": _read_seed(profile),
            "profile_bytes": _profile_size(profile)}
    if getattr(args, "json", False):
        print(json.dumps(info, ensure_ascii=False, indent=2))  # noqa: T201 - --json 输出就是打印
        return 0
    account = store.read_account(args.name)
    pairs = report.status_pairs(info) + report.account_pairs(account)
    fallback = report.status_lines(info) + [f"{k:<8}: {v}" for k, v in report.account_pairs(account)]
    for line in fallback:
        log_line(line)
    ui.emit_pairs(pairs, fallback=fallback, styles=report.status_styles(pairs))
    return 0


def cmd_set_exit(args) -> int:
    cfg = load_runtime()
    spec, _proxy = resolve_exit(args.exit, cfg)
    meta = store.read_meta(args.name)
    old = meta.get("exit")
    meta["exit"] = spec
    meta.pop("exit_ip", None)          # 换了出口，旧的 IP 记忆作废
    store.write_meta(args.name, meta)
    say(f"{args.name}: 出口 {old} → {spec}")
    if spec == "direct":
        say("         下次启动不设代理，走原生环境")
    return 0


def cmd_export(args) -> int:
    cfg = load_runtime()
    output = (Path(args.output).expanduser() if args.output
              else Path.cwd() / f"{args.name}.bundle.tar.gz")
    pin = build_pin(cfg)
    pin.pop("font.manifest", None)      # 清单由字体模式在本地重新生成，不写进包里
    return bundle.export_bundle(args.name, output, cfg, pin)


def cmd_import(args) -> int:
    cfg = load_runtime()
    _check_name(args.name)
    return bundle.import_bundle(Path(args.bundle), args.name, cfg)


def cmd_mv(args) -> int:
    load_runtime()
    for name in (args.old, args.new):
        _check_name(name)
    src, dst = paths.sandbox_dir(args.old), paths.sandbox_dir(args.new)
    if not (src / paths.META_FILE_NAME).is_file():
        die(f"没有这个沙盒: {args.old}")
    if dst.exists():
        die(f"{dst} 已存在")
    if process.browser_pids(src) or process.profile_pids(src):
        die("这个沙盒的浏览器还开着，先关掉再改名")
    src.rename(dst)
    meta = store.read_meta(args.new)
    meta["name"] = args.new
    store.write_meta(args.new, meta)
    say(f"已改名: {args.old} → {args.new}")
    return 0


def cmd_rm(args) -> int:
    load_runtime()
    d = paths.sandbox_dir(args.name)
    if not d.is_dir():
        die(f"没有这个沙盒: {args.name}")
    if not args.force:
        answer = input(f"删除 {d}（含 profile / cookie）？输入 yes 确认: ").strip().lower()
        if answer != "yes":
            say("已取消")
            return 1
    shutil.rmtree(d)
    say(f"已删除沙盒 {args.name}")
    return 0


def cmd_log(args) -> int:
    load_runtime()
    f = paths.log_file(args.name)
    if not f.is_file():
        say("还没有日志（启动一次后才会写）")
        return 0
    lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
    for line in lines[-args.lines:]:
        print(line)  # noqa: T201 - 日志内容原样打印到终端
    return 0


def _relay_available() -> bool:
    return importlib.util.find_spec("ccs.network.dns_relay") is not None


def _engine_version() -> str:
    try:
        from importlib import metadata  # noqa: PLC0415 - 只在 doctor 用到
        return metadata.version("invisible-playwright")
    except Exception:  # noqa: BLE001 - 没装就是空
        return ""


def _browser_ready() -> bool:
    cache = Path.home() / ".cache" / "invisible-playwright"
    return cache.is_dir() and any(cache.glob("firefox-*"))


def _tool(name: str) -> str:
    return "有" if shutil.which(name) else "无"


def cmd_doctor(_args) -> int:
    cfg = load_runtime()
    source = config.source_path()

    say(f"沙盒目录  : {paths.root()}")
    say(f"配置文件  : {source} {'有' if source.is_file() else '无（用内置默认）'}")
    say(f"venv      : {'有' if paths.venv_python().is_file() else '没有 .venv'}")
    say(f"DNS 中继  : {'有' if _relay_available() else '缺少 network/dns_relay.py'}")
    engine = _engine_version()
    say(f"引擎      : {engine or '未安装（make setup）'}"
        + (f" · 浏览器{'已下载' if _browser_ready() else '未下载（make setup）'}" if engine else ""))
    say(f"富文本    : {'rich ' + rich_version() if rich_version() else '未装（退回纯文本）'}")
    say(f"系统命令  : curl {_tool('curl')} · pgrep {_tool('pgrep')} · Xvfb {_tool('Xvfb')}"
        + ("" if shutil.which("Xvfb") else "（Xvfb 只在 headless 时用）"))
    build_pin(cfg)                    # 写错了在这里就报，不用等到启动
    say(f"屏幕      : {cfg.get('screen') or 'auto（按种子生成）'} @ {cfg.get('dpr') or 1}x")
    fonts_desc = ("全部（含简繁中文）" if str(cfg.get("fonts")).lower() == "all"
                  else "去掉简繁中文字体")
    say(f"字体      : {fonts_desc}")
    scheme = str(cfg.get("color_scheme") or "dark").lower()
    scheme_label = {"dark": "深色（固定）", "light": "浅色（固定）"}.get(scheme, "跟随种子")
    say(f"配色      : {scheme_label}")
    say(f"出口 IP   : {'严格：变了就不启动' if cfg.get('strict_ip', True) else '宽松：变了只提示'}")
    say("")
    default = str(cfg.get("exit") or "direct")
    say(f"出口      : {default}" + ("（没配置 = direct）" if default == "direct" else ""))
    return 0
