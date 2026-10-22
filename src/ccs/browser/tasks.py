"""浏览器任务：open / check / warmup / harvest。"""

from __future__ import annotations

import json
import random
import shutil
import time
from contextlib import suppress

from ccs import config, errors, paths
from ccs.browser import launcher, prefs, process
from ccs.errors import die, log_line, say
from ccs.network import DnsRelay, pick_exit, preflight, resolve_exit
from ccs.sandbox import store
from ccs.sandbox.lifecycle import config_timeouts, load_runtime

WEBRTC_JS = """
new Promise((res) => {
  const out = [];
  const pc = new RTCPeerConnection({iceServers:[{urls:'stun:stun.l.google.com:19302'}]});
  pc.createDataChannel('x');
  pc.onicecandidate = (e) => {
    if (!e.candidate) { res(out); return; }
    const m = e.candidate.candidate.match(/([0-9]{1,3}\\.){3}[0-9]{1,3}|[0-9a-f]{1,4}(:[0-9a-f]{0,4}){2,}/i);
    if (m) out.push(m[0]);
  };
  pc.createOffer().then(o => pc.setLocalDescription(o)).catch(() => res(out));
  setTimeout(() => res(out), 8000);
});
"""

HARVEST_JS = """
async () => {
  const get = async (u) => {
    try {
      const r = await fetch(u, { headers: { accept: 'application/json' } });
      if (!r.ok) return null;
      return await r.json();
    } catch (e) { return null; }
  };
  const [orgs, acct] = await Promise.all([get('/api/organizations'), get('/api/account')]);
  return { orgs, acct };
}
"""


def _timeouts(cfg: dict) -> dict:
    return cfg["browser"]["timeouts"]


def harvest_account(page, name: str) -> dict | None:
    """在 claude.ai 页面里抓账号信息。

    必须在浏览器里做：Cloudflare 会拦掉浏览器之外的请求（实测 curl 一律
    返回 "Just a moment..."），所以不能用外部 HTTP 拿。
    """
    try:
        data = page.evaluate(HARVEST_JS) or {}
    except Exception as exc:  # noqa: BLE001 - 页面脚本失败只能记日志，抓不到账号不算致命
        log_line(f"抓取账号失败: {type(exc).__name__}: {exc}")
        return None

    acct = data.get("acct") or {}
    orgs = data.get("orgs") or []
    if not acct and not orgs:
        return None                      # 没登录

    cookies = {}
    with suppress(Exception):
        for c in page.context.cookies("https://claude.ai"):
            cookies[c.get("name", "")] = c.get("value", "")

    active = cookies.get("lastActiveOrg", "")
    org = next((o for o in orgs if o.get("uuid") == active), orgs[0] if orgs else {})
    info = {
        "email": acct.get("email_address") or "",
        "display_name": acct.get("display_name") or "",
        "account_created_at": acct.get("created_at") or "",
        "org_id": org.get("uuid") or active,
        "org_name": org.get("name") or "",
        "org_created_at": org.get("created_at") or "",
        "plan": org.get("rate_limit_tier") or org.get("analytics_subscription_plan") or "",
        "plan_label": org.get("plan_display_name") or org.get("plan_display_label") or "",
        "billing_type": org.get("billing_type") or "",
        "session_key": cookies.get("sessionKey", ""),
        "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    if not store.write_account(name, info):
        log_line("写 account.json 失败")
        return None
    return info


def _find_input(page, cfg: dict) -> str:
    for candidate in ('div[contenteditable="true"]', '[data-testid="chat-input"]', "textarea"):
        try:
            page.wait_for_selector(candidate, timeout=_timeouts(cfg)["selector_ms"])
        except Exception:  # noqa: BLE001, S112 - 找不到这个选择器就试下一个，不记日志
            continue
        else:
            return candidate
    return ""


def do_warmup(page, prompt: str, cfg: dict) -> bool:
    """在已登录的页面里发一句话，等到有回复。"""
    warmup = cfg["warmup"]
    selector = _find_input(page, cfg)
    if not selector:
        log_line("养号失败：找不到输入框")
        return False

    try:
        before = len(page.inner_text("body"))
    except Exception:  # noqa: BLE001 - 页面读不到就按 0 算，继续尝试养号
        before = 0

    try:
        page.click(selector)
        page.keyboard.type(prompt, delay=warmup["type_delay_ms"])
        page.keyboard.press("Enter")
    except Exception as exc:  # noqa: BLE001 - 点击/输入失败只算这次养号失败
        log_line(f"养号失败：{type(exc).__name__}: {exc}")
        return False

    deadline = time.time() + warmup["reply_wait_s"]
    while time.time() < deadline:
        time.sleep(warmup["poll_interval_s"])
        try:
            if len(page.inner_text("body")) > before + warmup["min_growth"]:
                return True
        except Exception:  # noqa: BLE001 - 页面被关掉，养号直接算失败
            return False
    return False


def _headless_check(page, cfg: dict) -> int:
    say(f"页面出口 IP : {page.inner_text('body').strip()}")
    try:
        page.goto("https://www.cloudflare.com/", timeout=_timeouts(cfg)["goto_ms"])
        say(f"WebRTC 候选 : {json.dumps(page.evaluate(WEBRTC_JS))}")
    except Exception as exc:  # noqa: BLE001 - WebRTC 检测失败只提示，不影响自检退出
        say(f"WebRTC 检测失败: {exc}")
    return 0


def _say_account(page, name: str) -> None:
    if harvest_account(page, name):
        say(f"账号    : {store.read_account(name).get('email') or '已记录'}")


def warmup_flow(page, name: str, prompt: str | None, cfg: dict) -> int:
    prompts = cfg["warmup"]["prompts"] or config.WARM_PROMPTS
    prompt = prompt or random.choice(prompts)  # noqa: S311 - 只随机挑一句提示词，不用于安全用途
    say(f"养号    : 发送「{prompt}」")
    ok = do_warmup(page, prompt, cfg)
    say("养号    : 已收到回复" if ok else "养号    : 没等到回复（看 ccs.log）")
    log_line(f"养号结果: {'ok' if ok else 'timeout'} · {prompt}")
    harvest_account(page, name)
    return 0


def wait_loop(session, name: str, timeout: int, cfg: dict) -> int:
    say("")
    say("浏览器已打开。手工注册/养号完成后，直接关掉浏览器窗口即可退出。")
    t = _timeouts(cfg)
    pid = session.browser_pid
    deadline = time.time() + timeout if timeout else 0
    next_harvest = time.time() + t["harvest_interval_s"]
    while True:
        if deadline and time.time() > deadline:
            say("到达 --timeout，关闭浏览器")
            return 0
        if session.closed.is_set():
            say("窗口已关闭，沙盒数据已保留")
            return 0
        # 中途登录了就顺手记一下；没登录时接口返回空，不会写文件
        if pid and process.pid_alive(pid) and time.time() >= next_harvest:
            next_harvest = time.time() + t["harvest_interval_s"]
            harvest_account(session.page, name)
        # 浏览器崩溃时 wait_for_timeout 不会抛错，进程会一直挂着。主动看一眼。
        if pid and not process.pid_alive(pid):
            say("浏览器进程已经退出（可能是崩溃），沙盒数据已保留")
            log_line(f"浏览器 PID {pid} 不在了，按崩溃处理")
            return 0
        # 不用 page.wait_for_timeout：浏览器一崩它会永远挂着，
        # 这里只需要睡一秒，靠上面的 PID 检查发现问题。
        time.sleep(t["run_sleep_s"])


def _run_browser(args, proxy: str, cfg: dict, *, headless: bool, warmup: bool) -> int:
    name = args.name
    session = launcher.open_session(
        name, proxy, cfg,
        headless=headless,
        pin=prefs.build_pin(cfg),
    )
    try:
        trace = session.page.goto("https://api.ipify.org",
                                  timeout=_timeouts(cfg)["goto_ms"])
        say(f"页面加载完成（HTTP {trace.status if trace else '?'}）")
        if headless:
            return _headless_check(session.page, cfg)
        launcher.open_claude(session.page, cfg)
        _say_account(session.page, name)
        if warmup:
            prompt = str(getattr(args, "prompt", "") or "")
            return warmup_flow(session.page, name, prompt, cfg)
        return wait_loop(session, name, getattr(args, "timeout", 0), cfg)
    finally:
        launcher.close_session(session.context, session.profile, cfg)


def run(args, *, headless: bool, warmup: bool = False) -> int:
    cfg = load_runtime()
    meta = store.read_meta(args.name)
    errors.set_log_path(paths.log_file(args.name))
    log_line(f"=== {'check' if headless else 'start'} {args.name} ===")
    spec, proxy = resolve_exit(pick_exit(args, meta), cfg)   # proxy 为空 = direct
    relay = None
    try:
        if proxy.startswith(("socks5://", "socks5h://")):
            relay = DnsRelay(proxy, cfg, log=log_line)
            proxy = relay.start()
            say("DNS     : 沙盒自带中继（DoH 查域名，代理只收 IP）")
        elif proxy:
            say("DNS     : 由代理自己解析（中继只支持 socks5 上游）")
        process.clear_hooks()
        if relay:
            process.add_hook(relay.stop)
        process.add_hook(
            lambda: process.shutdown_browser(
                paths.profile_dir(args.name), config_timeouts(cfg)["close_grace_s"]))
        ip = preflight(
            args.name, spec, proxy, cfg,
            die_on_change=(bool(cfg.get("strict_ip", True))
                           and not bool(getattr(args, "allow_ip_change", False))))
        if ip.startswith("("):
            die("出口不通，先别继续")
        if headless and not _has_xvfb():
            die("headless 需要 Xvfb：sudo apt install -y xvfb")
        return _run_browser(args, proxy, cfg, headless=headless, warmup=warmup)
    finally:
        if relay:
            relay.stop()


def _has_xvfb() -> bool:
    return shutil.which("Xvfb") is not None


def cmd_web(args) -> int:
    return run(args, headless=False)


def cmd_check(args) -> int:
    return run(args, headless=True)


def cmd_warmup(args) -> int:
    return run(args, headless=getattr(args, "headless", False), warmup=True)
