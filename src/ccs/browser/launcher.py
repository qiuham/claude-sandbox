"""浏览器启动器：建持久化上下文、准备页面、关窗口时收摊。"""

from __future__ import annotations

import os
import threading
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ccs import paths
from ccs.browser import deps, process
from ccs.browser.prefs import engine_class
from ccs.errors import die, log_line, say
from ccs.network import stun
from ccs.sandbox import store

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class Session:
    context: object
    page: object
    closed: threading.Event
    browser_pid: int
    profile: Path
    started_at: float = 0.0


def _timeouts(cfg: dict) -> dict:
    return cfg["browser"]["timeouts"]


def quiet_exit(bctx) -> None:
    with suppress(Exception):
        bctx.__exit__(None, None, None)


def unfix_viewport(bctx) -> None:
    """让页面跟着窗口重排。

    Playwright 默认把视口钉死成声明的屏幕大小，拖窗口页面不动。
    把 persistent context 的 viewport 去掉、换成 no_viewport，页面就回到
    真浏览器的行为：窗口多大，页面多大。
    """
    original = bctx._persistent_context_kwargs      # noqa: SLF001 - 引擎没有公开开关

    def patched():
        kwargs = dict(original())
        kwargs.pop("viewport", None)
        kwargs["no_viewport"] = True
        return kwargs

    bctx._persistent_context_kwargs = patched  # noqa: SLF001 - 引擎没有公开开关


def _make_context(engine, kw: dict, *, fit_window: bool):
    bctx = engine(**kw)
    if fit_window:
        unfix_viewport(bctx)
    return bctx


def open_browser(profile: Path, engine, kw: dict, *, fit_window: bool, cfg: dict):
    """进入持久化上下文。profile 被占用时清场重试一次。"""
    grace = _timeouts(cfg)["close_grace_s"]
    bctx = _make_context(engine, kw, fit_window=fit_window)
    try:
        return bctx, bctx.__enter__()
    except Exception as exc:
        text = str(exc)
        # 引擎缺系统库时直接退出，报的是共享库加载失败，不是 Python 错误
        if "shared object file" in text or "XPCOM" in text:
            info = deps.hint()
            if info:
                die(info)
        if "already running" not in text.lower():
            raise
        say("浏览器说 profile 被占用，清场后重试一次")
        process.prepare_profile(profile, grace)
        bctx = _make_context(engine, kw, fit_window=fit_window)
        return bctx, bctx.__enter__()


def browser_pid(profile: Path) -> int:
    main_pids = process.browser_pids(profile)
    pid = main_pids[0] if main_pids else 0
    log_line(f"浏览器主进程 PID = {pid}（候选 {main_pids}）")
    return pid


def _attach_close_events(page, ctx) -> threading.Event:
    # 关窗口时 Firefox 进程不一定退出（实测会留着），PID 判断不到。
    # 靠 Playwright 自己的事件：页面/上下文一关就置位。
    closed = threading.Event()
    for target in (page, ctx):
        with suppress(Exception):
            target.on("close", lambda *_: closed.set())
    return closed


def _attach_log_events(page) -> None:
    # 浏览器这边的错误也写进同一份日志：控制台 error 和未捕获异常
    try:
        page.on("console", lambda m: log_line(f"[console:{m.type}] {m.text}")
                if m.type in ("error", "warning") else None)
        page.on("pageerror", lambda exc: log_line(f"[pageerror] {exc}"))
    except Exception as exc:  # noqa: BLE001 - 注册日志监听失败不能影响启动
        log_line(f"[warn] 注册浏览器日志失败: {exc}")


def _prepare_page(browser, cfg: dict):
    ctx = browser.contexts[0] if hasattr(browser, "contexts") and browser.contexts else browser
    page = ctx.new_page()
    page.set_default_timeout(_timeouts(cfg)["page_ms"])
    closed = _attach_close_events(page, ctx)
    _attach_log_events(page)
    return page, closed


def open_claude(page, cfg: dict) -> None:
    # 打开首页失败不算启动失败：窗口已经在了，用户自己输网址就行。
    try:
        page.goto("https://claude.ai/", timeout=_timeouts(cfg)["goto_ms"])
    except Exception as exc:  # noqa: BLE001 - 页面打不开不算启动失败，窗口已经在
        say(f"打开 claude.ai 失败: {type(exc).__name__}（窗口已打开，可以手动输入网址）")
        log_line(f"打开 claude.ai 失败: {type(exc).__name__}: {exc}")


def close_session(bctx, profile: Path, cfg: dict) -> None:
    """关闭浏览器放到线程里，最多等 5 秒。

    实测：Ctrl-C 或关窗口之后，Playwright 的关闭会卡住两分多钟。
    """
    t = _timeouts(cfg)
    closer = threading.Thread(target=quiet_exit, args=(bctx,), daemon=True)
    closer.start()
    closer.join(t["close_join_s"])
    if closer.is_alive():
        say("关闭超时，强制结束浏览器")
        process.stop_browser(profile, grace=t["close_grace_s"])
        closer.join(t["close_join_extra_s"])
    # 正常关闭时 Firefox 自己会删锁；被强杀时不会，这里补一刀
    if not process.profile_pids(profile):
        process.clear_lock_files(profile)


def webrtc_declared_ip(proxy: str, exit_ip: str) -> str:
    """要不要把 srflx 候选钉成出口 IP；返回空串表示不用管。

    有 SOCKS5 代理时，Firefox 会把 STUN 的 UDP 也交给代理，真实候选就是出口 IP。
    direct 时先量一次 UDP 出口：
      - 和 TCP 出口一样（例如 Clash TUN），真实候选本来就对，不用管；
      - 不一样（系统代理只转 TCP）或者量不到，就声明成出口 IP，
        页面看到的 WebRTC 地址和出口一致，不会漏出本机 IP。
    """
    if not exit_ip or exit_ip.startswith("("):
        return ""
    if proxy.startswith(("socks5://", "socks5h://")):
        return ""
    if not proxy:
        udp_ip = stun.mapped_ip()
        if udp_ip and udp_ip == exit_ip:
            return ""
    return exit_ip


def _context_kwargs(profile: Path, *, headless: bool, proxy: str,
                    pin: dict | None, cfg: dict) -> dict:
    kw = {
        "seed": store.seed_of(profile)[0],
        "headless": headless,
        "humanize": float(cfg.get("humanize_s") or 1.5),
        "locale": "auto",              # 按出口 IP 的地区选语言
        "profile_dir": str(profile),
        "extra_prefs": cfg["browser"]["prefs"],
    }
    if proxy:
        kw["proxy"] = {"server": proxy.replace("socks5h://", "socks5://")}
    if pin:
        kw["pin"] = pin
    return kw


def open_session(name: str, proxy: str, cfg: dict, *,  # noqa: PLR0913 - 启动参数就是这些，拆对象反而绕
                 headless: bool, pin: dict | None, exit_ip: str = "") -> Session:
    engine = engine_class()
    profile = paths.profile_dir(name)
    profile.mkdir(parents=True, exist_ok=True)
    process.prepare_profile(profile, grace=_timeouts(cfg)["close_grace_s"])
    # 双保险：即使某个版本不认偏好，也别让崩溃报告器冒出来
    os.environ["MOZ_CRASHREPORTER_NO_REPORT"] = "1"   # 留 dump，不弹窗
    # WSLg 的 Wayland 合成器会断，Firefox 跟着以 SIGSEGV 退出
    # （Error reading events from display: Broken pipe）。走 XWayland 更稳。
    os.environ.setdefault("MOZ_ENABLE_WAYLAND", "0")
    # XWayland 下由 Firefox 自己画标题栏，不交给 WSLg 的窗口管理器，
    # 否则会套一圈白色边框。
    os.environ.setdefault("MOZ_GTK_TITLEBAR_DECORATION", "client")
    # WebRTC：该声明就声明成出口 IP，不该声明就把上一个会话留下的值清掉
    declared = webrtc_declared_ip(proxy, exit_ip)
    if declared:
        os.environ["STEALTHFOX_WEBRTC_PUBLIC_IP"] = declared
        log_line(f"WebRTC  srflx 声明为 {declared}")
    else:
        os.environ.pop("STEALTHFOX_WEBRTC_PUBLIC_IP", None)
    kw = _context_kwargs(profile, headless=headless, proxy=proxy, pin=pin, cfg=cfg)
    fit_window = bool(cfg.get("fit_window", True))
    bctx, browser = open_browser(profile, engine, kw,
                                 fit_window=fit_window, cfg=cfg)
    pid = browser_pid(profile)
    page, closed = _prepare_page(browser, cfg)
    return Session(context=bctx, page=page, closed=closed, browser_pid=pid,
                   profile=profile, started_at=time.time())
