"""浏览器会话：启动、等待关闭、崩溃说明。"""

from __future__ import annotations

import shutil
import time

from ccs import errors, paths, ui
from ccs.browser import crash, launcher, prefs, process
from ccs.browser.account import harvest_account, say_account
from ccs.browser.chat import warmup_flow
from ccs.browser.check import headless_check
from ccs.browser.flows import refresh_flow
from ccs.browser.record import Recorder
from ccs.browser.reg import register
from ccs.config import config_timeouts
from ccs.errors import die, log_line, say
from ccs.network import DnsRelay, pick_exit, preflight, resolve_exit
from ccs.sandbox import store
from ccs.sandbox.runtime import load_runtime


def record_enabled(args) -> bool:
    """要不要录制：看 `--record`。所有会开窗口的命令都有这个参数。"""
    return bool(getattr(args, "record", False))


def run(args, *, mode: str) -> int:
    cfg = load_runtime()
    meta = store.read_meta(args.name)
    if mode == "login" and not (str(getattr(args, "email", "") or "").strip()
                                or store.read_account(args.name).get("email")):
        # 启动浏览器之前就拦下：不然会开一个窗口再关掉，看起来像坏了。
        die(f"{args.name} 没登记过邮箱，没法重新登录；注册请用 ccs reg {args.name}")
    errors.set_log_path(paths.log_file(args.name))
    log_line(f"=== {mode} {args.name} ===")
    spec, proxy = resolve_exit(pick_exit(args, meta), cfg)   # proxy 为空 = direct
    relay = None
    try:
        if proxy.startswith(("socks5://", "socks5h://")):
            relay = DnsRelay(proxy, cfg, log=log_line)
            proxy = relay.start()
            say(ui.kv("DNS", "沙盒自带中继（DoH 查域名，代理只收 IP）"))
        elif proxy:
            say(ui.kv("DNS", "由代理自己解析（中继只支持 socks5 上游）"))
        process.clear_hooks()
        if relay:
            process.add_hook(relay.stop)
        process.add_hook(
            lambda: process.shutdown_browser(
                paths.profile_dir(args.name), config_timeouts(cfg)["close_grace_s"]))
        ip = preflight(
            args.name, spec, proxy, cfg,
            die_on_change=(bool(cfg.get("strict_ip", True))
                           and not bool(getattr(args, "allow_ip", False))),
            allow_region=bool(getattr(args, "allow_region", False)))
        if ip.startswith("("):
            die("出口不通，先别继续。\n"
                "           多半是 DoH 查不了域名（出口节点挡了 DoH），先换节点或检查 Clash 的 DNS 配置。")
        if mode == "check" and not has_xvfb():
            die("headless 需要 Xvfb：sudo apt install -y xvfb")
        return run_browser(args, proxy, cfg, mode=mode, exit_ip=ip)
    finally:
        if relay:
            relay.stop()


def run_browser(args, proxy: str, cfg: dict, *,  # noqa: PLR0911 - 每种模式单独返回
                mode: str, exit_ip: str) -> int:
    name = args.name
    session = launcher.open_session(
        name, proxy, cfg,
        headless=(mode == "check"),
        pin=prefs.build_pin(cfg, store.read_meta(name).get("pin"),
                            store.read_meta(name).get("fonts")),
        exit_ip=exit_ip,
    )
    recorder = None
    if record_enabled(args):
        recorder = Recorder(paths.sandbox_dir(name) / "record")
        recorder.start(session.page)
        say(ui.kv("录制", f"{recorder.dir}（关窗后看 actions.jsonl）"))
    try:
        if mode == "onboard":
            # 只走 onboarding：适合"已经登录、但引导没走完"的号
            from ccs.browser.onboarding import complete as complete_onboarding  # noqa: PLC0415
            launcher.open_claude(session.page, cfg)
            result = complete_onboarding(
                session.page, name, timeout=300,
                tick=recorder.poll if recorder else None)
            say(ui.kv("onboarding", {
                "ok": "走完了", "closed": "窗口已关闭", "stuck": "卡住了",
                "timeout": "超时没走完"}.get(result, result)))
            return 0 if result == "ok" else 1
        if mode in ("reg", "login"):
            # 注册/登录从 claude.ai 直接开始，不先跳自检页：多出来的跳转看起来就像脚本。
            return register(session.page, name, cfg,
                            str(getattr(args, "email", "") or ""),
                            login_only=(mode == "login"),
                            tick=recorder.poll if recorder else None)
        trace = session.page.goto("https://api.ipify.org",
                                  timeout=config_timeouts(cfg)["goto_ms"])
        say(f"页面加载完成（HTTP {trace.status if trace else '?'}）")
        if mode == "check":
            return headless_check(session.page, cfg, exit_ip)
        launcher.open_claude(session.page, cfg)
        if mode == "refresh":
            return refresh_flow(session.page, name)
        say_account(session.page, name)
        if mode == "warmup":
            prompt = str(getattr(args, "prompt", "") or "")
            return warmup_flow(session.page, name, prompt, cfg)
        return wait_loop(session, name, getattr(args, "timeout", 0), cfg, recorder)
    except Exception as exc:  # noqa: BLE001 - 流程里出意外时，窗口先别关
        log_line(f"流程出错: {type(exc).__name__}: {exc}")
        say(ui.kv("出错", f"{type(exc).__name__}: {exc}"))
        say(ui.kv("处理", "窗口先留着，你可以在窗口里继续操作；关掉窗口后退出"))
        return wait_loop(session, name, 0, cfg, recorder)
    finally:
        if recorder is not None:
            recorder.poll(session.page)     # 收尾：把最后攒的事件也记下来
        launcher.close_session(session.context, session.profile, cfg)


def wait_loop(session, name: str, timeout: int, cfg: dict,
              recorder: Recorder | None = None) -> int:
    say("")
    say("浏览器已打开。注册或日常对话完成后，直接关掉浏览器窗口即可退出。")
    t = config_timeouts(cfg)
    pid = session.browser_pid
    deadline = time.time() + timeout if timeout else 0
    next_harvest = time.time() + t["harvest_interval_s"]
    while True:
        if deadline and time.time() > deadline:
            say("到达 --timeout，关闭浏览器")
            return 0
        if session.closed.is_set():
            if pid and not process.pid_alive(pid):
                return say_crash(session, pid)
            say("窗口已关闭，沙盒数据已保留")
            return 0
        # 中途登录了就顺手记一下；没登录时接口返回空，不会写文件
        if pid and process.pid_alive(pid) and time.time() >= next_harvest:
            next_harvest = time.time() + t["harvest_interval_s"]
            harvest_account(session.page, name)
        if recorder is not None:
            recorder.poll(session.page)
        # 浏览器崩溃时 wait_for_timeout 不会抛错，进程会一直挂着。主动看一眼。
        if pid and not process.pid_alive(pid):
            return say_crash(session, pid)
        # 不用 page.wait_for_timeout：浏览器一崩它会永远挂着，
        # 这里只需要睡一秒，靠上面的 PID 检查发现问题。
        # 录制时睡得短一点，事件和截图的对应关系才准确。
        time.sleep(0.3 if recorder is not None else t["run_sleep_s"])


def say_crash(session, pid: int) -> int:
    """浏览器没了：说清楚原因，别让用户以为是账号问题。"""
    reason = crash.latest_reason(session.started_at)
    say("浏览器进程已经退出（可能是崩溃），沙盒数据已保留")
    if reason:
        say(ui.kv("崩溃原因", reason[:160]))
        hint = crash.explain(reason)
        if hint:
            say(ui.indent(hint))
    log_line(f"浏览器 PID {pid} 不在了，按崩溃处理；原因 {reason[:200]}")
    return 0


def has_xvfb() -> bool:
    return shutil.which("Xvfb") is not None
