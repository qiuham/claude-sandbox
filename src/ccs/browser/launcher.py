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


def session_prefs(cfg: dict, proxy: str) -> dict:
    """按出口决定 WebRTC 怎么办。

    有 SOCKS5 代理时，Firefox 会把 STUN 的 UDP 也交给代理，
    ICE 拿到的就是出口 IP —— 和真实用户一样，不用干预。
    没有代理（或只有 HTTP 代理，UDP 过不去）时，STUN 会拿到真实公网 IP，
    这时只允许代理候选：候选为空，ipleak 上看不到任何 IP。
    """
    prefs = dict(cfg["browser"]["prefs"])
    if not proxy or proxy.startswith(("http://", "https://")):
        prefs["media.peerconnection.ice.proxy_only"] = True
        prefs["media.peerconnection.ice.no_host"] = True
    return prefs


def _context_kwargs(profile: Path, *, headless: bool, proxy: str,
                    pin: dict | None, cfg: dict) -> dict:
    kw = {
        "seed": store.seed_of(profile)[0],
        "headless": headless,
        "humanize": float(cfg.get("humanize_s") or 1.5),
        "locale": "auto",              # 按出口 IP 的地区选语言
        "profile_dir": str(profile),
        "extra_prefs": session_prefs(cfg, proxy),
    }
    if proxy:
        kw["proxy"] = {"server": proxy.replace("socks5h://", "socks5://")}
    if pin:
        kw["pin"] = pin
    return kw


def open_session(name: str, proxy: str, cfg: dict, *,
                 headless: bool, pin: dict | None) -> Session:
    engine = engine_class()
    profile = paths.profile_dir(name)
    profile.mkdir(parents=True, exist_ok=True)
    process.prepare_profile(profile, grace=_timeouts(cfg)["close_grace_s"])
    # 双保险：即使某个版本不认偏好，也别让崩溃报告器冒出来
    os.environ["MOZ_CRASHREPORTER_NO_REPORT"] = "1"   # 留 dump，不弹窗
    # WSLg 的 Wayland 合成器会断，Firefox 跟着以 SIGSEGV 退出
    # （Error reading events from display: Broken pipe）。走 XWayland 更稳。
    os.environ.setdefault("MOZ_ENABLE_WAYLAND", "0")
    kw = _context_kwargs(profile, headless=headless, proxy=proxy, pin=pin, cfg=cfg)
    fit_window = bool(cfg.get("fit_window", True))
    bctx, browser = open_browser(profile, engine, kw,
                                 fit_window=fit_window, cfg=cfg)
    pid = browser_pid(profile)
    page, closed = _prepare_page(browser, cfg)
    return Session(context=bctx, page=page, closed=closed, browser_pid=pid,
                   profile=profile, started_at=time.time())
