"""浏览器任务：open / check / warmup / harvest。"""

from __future__ import annotations

import csv
import io
import ipaddress
import json
import random
import shutil
import time
from contextlib import suppress
from pathlib import Path

from ccs import config, errors, paths
from ccs.browser import crash, launcher, prefs, process
from ccs.errors import die, log_line, say
from ccs.network import DnsRelay, pick_exit, preflight, resolve_exit
from ccs.sandbox import store
from ccs.sandbox.lifecycle import config_timeouts, load_runtime

# 注意：整段不能以分号结尾。Playwright 会把表达式包进括号，末尾分号会变成语法错误。
WEBRTC_JS = """
new Promise((res) => {
  const out = [];
  const pc = new RTCPeerConnection({iceServers:[{urls:'stun:stun.l.google.com:19302'}]});
  pc.createDataChannel('x');
  pc.onicecandidate = (e) => {
    const c = e.candidate && e.candidate.candidate;
    if (!c) { res(out); return; }
    out.push(c);
  };
  pc.createOffer().then(o => pc.setLocalDescription(o)).catch(() => res(out));
  setTimeout(() => res(out), 8000);
})
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
    merged = {**store.read_account(name), **info}   # 保留养号统计、存活记录等字段
    if not store.write_account(name, merged):
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


def _type_like_human(page, text: str, warmup: dict) -> None:
    """逐字输入：每字抖动、标点停顿、每隔一段停一下像在想。

    Playwright 的 delay 是固定值，一眼机器；这里所有间隔都带随机。
    """
    base = int(warmup["type_delay_ms"])
    jitter = int(warmup.get("type_jitter_ms") or 0)
    every = int(warmup.get("think_every_chars") or 0)
    think_min = int(warmup.get("think_pause_min_ms") or 0)
    think_max = int(warmup.get("think_pause_max_ms") or think_min)

    time.sleep(random.uniform(0.2, 0.6))  # noqa: S311 - 聚焦后的反应时间
    for index, char in enumerate(text):
        page.keyboard.type(char)
        pause = base + random.randint(-jitter, jitter)  # noqa: S311 - 打字节奏抖动
        if char in "，。！？,.!?":
            pause += base
        if every and index and index % every == 0:
            pause += random.randint(think_min, max(think_min, think_max))  # noqa: S311 - 思考停顿
        time.sleep(max(0, pause) / 1000)


def _click_like_human(page, selector: str, warmup: dict) -> None:
    """移到元素内部一个随机点，停一下再点，而不是精确点中心。"""
    mouse = getattr(page, "mouse", None)
    locator = getattr(page, "locator", None)
    if mouse is None or locator is None:      # 测试里的假页面
        page.click(selector)
        return
    try:
        box = locator(selector).first.bounding_box()
    except Exception:  # noqa: BLE001 - 拿不到位置就退回普通点击
        box = None
    if not box:
        page.click(selector)
        return
    x = box["x"] + box["width"] * random.uniform(0.25, 0.75)   # noqa: S311
    y = box["y"] + box["height"] * random.uniform(0.3, 0.7)    # noqa: S311
    mouse.move(x, y)                                           # 引擎按种子生成曲线轨迹
    low = int(warmup.get("click_dwell_min_ms") or 0)
    high = int(warmup.get("click_dwell_max_ms") or low)
    time.sleep(random.randint(low, max(low, high)) / 1000)     # noqa: S311
    mouse.click(x, y)
    # 随机点可能落在输入框的空隙上，没拿到焦点就退回精确点击
    try:
        focused = page.evaluate(
            "() => !!document.activeElement && "
            "(document.activeElement.isContentEditable || "
            "document.activeElement.tagName === 'TEXTAREA')")
    except Exception:  # noqa: BLE001 - 判断不了就不管
        return
    if not focused:
        page.click(selector)


def _wait_for_text(page, needle: str, cfg: dict, timeout: float) -> bool:
    """等页面上出现某段文字（用来确认消息真的发出去了）。"""
    warmup = cfg["warmup"]
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(warmup["poll_interval_s"])
        try:
            if needle in page.inner_text("body"):
                return True
        except Exception:  # noqa: BLE001 - 页面被关掉就当没等到
            return False
    return False


def _wait_for_growth(page, base: int, cfg: dict) -> bool:
    """等页面文本继续增长（用来确认模型回复了）。"""
    warmup = cfg["warmup"]
    deadline = time.time() + warmup["reply_wait_s"]
    while time.time() < deadline:
        time.sleep(warmup["poll_interval_s"])
        try:
            if len(page.inner_text("body")) > base + warmup["min_growth"]:
                return True
        except Exception:  # noqa: BLE001 - 页面被关掉就当没等到
            return False
    return False


def do_warmup(page, prompt: str, cfg: dict) -> str:
    """发一句话并等回复。返回 ok / no_input / not_sent / no_reply 四种结果。"""
    warmup = cfg["warmup"]
    selector = _find_input(page, cfg)
    if not selector:
        return "no_input"

    try:
        _click_like_human(page, selector, warmup)
        _type_like_human(page, prompt, warmup)
        page.keyboard.press("Enter")
    except Exception as exc:  # noqa: BLE001 - 点击/输入失败只算这次养号失败
        log_line(f"养号失败：{type(exc).__name__}: {exc}")
        return "not_sent"

    # 第一关：消息必须出现在页面上，否则只是键盘输入没生效
    if not _wait_for_text(page, prompt, cfg, warmup["send_confirm_s"]):
        return "not_sent"

    try:
        before = len(page.inner_text("body"))
    except Exception:  # noqa: BLE001 - 读不到就按 0 算
        before = 0
    return "ok" if _wait_for_growth(page, before, cfg) else "no_reply"


def webrtc_public_ips(candidates: list[str]) -> list[str]:
    """候选里的公网地址。mDNS 名字（*.local）和私有地址都不算。"""
    found: list[str] = []
    for line in candidates:
        for token in line.split():
            try:
                addr = ipaddress.ip_address(token)
            except ValueError:
                continue
            if addr.is_global and str(addr) not in found:
                found.append(str(addr))
    return found


def webrtc_verdict(candidates: list[str], exit_ip: str) -> str:
    """一句话结论：没拿到公网 IP / 只有出口 IP / 有别的公网 IP（泄露）。"""
    ips = webrtc_public_ips(candidates)
    if not ips:
        return "没拿到公网 IP（不泄露）"
    leaked = [ip for ip in ips if ip != exit_ip]
    if leaked:
        return f"泄露！候选里有 {', '.join(leaked)}，和出口 {exit_ip} 不同"
    return "只有出口 IP，正常"


def _headless_check(page, cfg: dict, exit_ip: str) -> int:
    say(f"页面出口 IP : {page.inner_text('body').strip()}")
    try:
        page.goto("https://www.cloudflare.com/", timeout=_timeouts(cfg)["goto_ms"])
        candidates = page.evaluate(WEBRTC_JS)
        say(f"WebRTC 候选 : {json.dumps(candidates, ensure_ascii=False)}")
        say(f"WebRTC 结论 : {webrtc_verdict(candidates, exit_ip)}")
    except Exception as exc:  # noqa: BLE001 - WebRTC 检测失败只提示，不影响自检退出
        say(f"WebRTC 检测失败: {exc}")
    return 0


def _say_account(page, name: str) -> None:
    if harvest_account(page, name):
        say(f"账号    : {store.read_account(name).get('email') or '已记录'}")


def _pick_prompt(name: str, prompts: list[str], cfg: dict) -> str:
    """挑一句：daily 按日期轮换，random 随机但不连着用同一句。"""
    if not prompts:
        return ""
    mode = str(cfg["warmup"].get("pick") or "random").lower()
    last = str(store.read_account(name).get("last_warmup_prompt") or "")

    if mode == "daily":
        index = time.localtime().tm_yday % len(prompts)
        candidate = prompts[index]
        if candidate == last and len(prompts) > 1:
            candidate = prompts[(index + 1) % len(prompts)]
        return candidate

    candidates = [p for p in prompts if p != last] or prompts
    return random.choice(candidates)  # noqa: S311 - 只挑提示词，不用于安全用途


def _remember_prompt(name: str, prompt: str) -> None:
    """记下这次用了哪句，避免下次重复。"""
    account = store.read_account(name)
    if not account:
        return
    account["last_warmup_prompt"] = prompt
    account["last_warmup_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    account["warmup_count"] = int(account.get("warmup_count") or 0) + 1
    store.write_account(name, account)


REFRESH_TEXT = {
    "ok": "会话正常，账号信息已刷新",
    "invalid": "会话已失效（要重新登录）",
    "restricted": "账号受限（落在 /restricted）",
}


def refresh_flow(page, name: str) -> int:
    """探测会话是否还活着；活着就顺手把账号信息刷新一遍。"""
    url = str(getattr(page, "url", "") or "")
    if "/restricted" in url:
        state = "restricted"
    elif "/login" in url or "/signin" in url:
        state = "invalid"
    else:
        state = "ok"

    if state == "ok":
        harvest_account(page, name)

    account = store.read_account(name)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    account["alive_state"] = state
    account["alive_checked_at"] = now
    if state == "ok" and not account.get("alive_first_ok_at"):
        account["alive_first_ok_at"] = now
    if state != "ok" and not account.get("alive_first_invalid_at"):
        account["alive_first_invalid_at"] = now
    store.write_account(name, account)

    say(f"状态    : {REFRESH_TEXT[state]}")
    say(f"检测时间: {now}")
    if account.get("alive_first_ok_at"):
        say(f"首次正常: {account['alive_first_ok_at']}")
    if account.get("alive_first_invalid_at"):
        say(f"首次失效: {account['alive_first_invalid_at']}")
    log_line(f"刷新结果: {state} · {account.get('email', '')}")
    return 0 if state == "ok" else 1


WARMUP_TEXT = {
    "ok": "已收到回复",
    "no_input": "找不到输入框（可能没登录）",
    "not_sent": "消息没发出去（可能没登录）",
    "no_reply": "发了但没等到回复（看 ccs.log）",
}


def _parse_csv(text: str) -> list[str]:
    """CSV：取第一列；首行是表头（prompt/text/内容）时跳过。

    用标准库 csv 解析，所以带引号的英文逗号、嵌入换行都能处理。
    """
    rows = [row for row in csv.reader(io.StringIO(text)) if row and row[0].strip()]
    if not rows:
        return []
    first = rows[0][0].strip().lower()
    if first in ("prompt", "text", "content", "内容", "提示词"):
        rows = rows[1:]
    return [row[0].strip() for row in rows if row[0].strip()]


def _parse_prompt_file(text: str) -> list[str]:
    """解析话术文件。

    - 有单独一行 `---`：按这个分隔成多段，段内可以多行（适合较长的提示词）
    - 没有分隔线：一行一句
    - `#` 开头的行和空行忽略
    """
    lines = text.splitlines()
    if any(line.strip() == "---" for line in lines):
        blocks, current = [], []
        for line in lines:
            if line.strip() == "---":
                block = "\n".join(current).strip()
                if block:
                    blocks.append(block)
                current = []
            elif not line.strip().startswith("#"):
                current.append(line)
        block = "\n".join(current).strip()
        if block:
            blocks.append(block)
        return blocks

    return [line.strip() for line in lines
            if line.strip() and not line.strip().startswith("#")]


def _strip_frontmatter(text: str) -> str:
    """去掉文件开头的 YAML frontmatter（--- 包起来那段）。"""
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return text
    rest = stripped[3:]
    end = rest.find("\n---")
    if end < 0:
        return text
    return rest[end + 4:].lstrip("\n")


def _read_prompt_dir(folder: Path) -> list[str]:
    """一个目录，一个文件一句提示词；按文件名排序。"""
    prompts = []
    for file in sorted(list(folder.glob("*.md")) + list(folder.glob("*.txt"))):
        try:
            body = _strip_frontmatter(file.read_text(encoding="utf-8")).strip()
        except OSError:
            continue
        if body:
            prompts.append(body)
    return prompts


def _prompts(cfg: dict) -> list[str]:
    """话术来源：先看 prompts_file（一行一句），没有就用配置里的列表。"""
    path = str(cfg["warmup"].get("prompts_file") or "").strip()
    if path:
        file = Path(path).expanduser()
        if file.is_file():
            raw = file.read_text(encoding="utf-8")
            blocks = _parse_csv(raw) if file.suffix.lower() == ".csv" else _parse_prompt_file(raw)
            if blocks:
                return blocks
    folder = str(cfg["warmup"].get("prompts_dir") or "").strip()
    if folder:
        directory = Path(folder).expanduser()
        if directory.is_dir():
            blocks = _read_prompt_dir(directory)
            if blocks:
                return blocks
    return cfg["warmup"]["prompts"] or config.WARM_PROMPTS


def warmup_flow(page, name: str, prompt: str | None, cfg: dict) -> int:
    prompts = _prompts(cfg)
    prompt = (prompt or _pick_prompt(name, prompts, cfg)).strip()
    say(f"养号    : 发送「{prompt}」")
    result = do_warmup(page, prompt, cfg)
    say(f"养号    : {WARMUP_TEXT.get(result, result)}")
    log_line(f"养号结果: {result} · {prompt}")
    harvest_account(page, name)
    if result == "ok":
        _remember_prompt(name, prompt)
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
            if pid and not process.pid_alive(pid):
                return _say_crash(session, pid)
            say("窗口已关闭，沙盒数据已保留")
            return 0
        # 中途登录了就顺手记一下；没登录时接口返回空，不会写文件
        if pid and process.pid_alive(pid) and time.time() >= next_harvest:
            next_harvest = time.time() + t["harvest_interval_s"]
            harvest_account(session.page, name)
        # 浏览器崩溃时 wait_for_timeout 不会抛错，进程会一直挂着。主动看一眼。
        if pid and not process.pid_alive(pid):
            return _say_crash(session, pid)
        # 不用 page.wait_for_timeout：浏览器一崩它会永远挂着，
        # 这里只需要睡一秒，靠上面的 PID 检查发现问题。
        time.sleep(t["run_sleep_s"])


def _say_crash(session, pid: int) -> int:
    """浏览器没了：说清楚原因，别让用户以为是账号问题。"""
    reason = crash.latest_reason(session.started_at)
    say("浏览器进程已经退出（可能是崩溃），沙盒数据已保留")
    if reason:
        say(f"崩溃原因: {reason[:160]}")
        hint = crash.explain(reason)
        if hint:
            say(f"           {hint}")
    log_line(f"浏览器 PID {pid} 不在了，按崩溃处理；原因 {reason[:200]}")
    return 0


def _run_browser(args, proxy: str, cfg: dict, *, mode: str, exit_ip: str) -> int:
    name = args.name
    session = launcher.open_session(
        name, proxy, cfg,
        headless=(mode == "check"),
        pin=prefs.build_pin(cfg, store.read_meta(name).get("pin"),
                            store.read_meta(name).get("fonts")),
        exit_ip=exit_ip,
    )
    try:
        trace = session.page.goto("https://api.ipify.org",
                                  timeout=_timeouts(cfg)["goto_ms"])
        say(f"页面加载完成（HTTP {trace.status if trace else '?'}）")
        if mode == "check":
            return _headless_check(session.page, cfg, exit_ip)
        launcher.open_claude(session.page, cfg)
        if mode == "refresh":
            return refresh_flow(session.page, name)
        _say_account(session.page, name)
        if mode == "warmup":
            prompt = str(getattr(args, "prompt", "") or "")
            return warmup_flow(session.page, name, prompt, cfg)
        return wait_loop(session, name, getattr(args, "timeout", 0), cfg)
    finally:
        launcher.close_session(session.context, session.profile, cfg)


def run(args, *, mode: str) -> int:
    cfg = load_runtime()
    meta = store.read_meta(args.name)
    errors.set_log_path(paths.log_file(args.name))
    log_line(f"=== {mode} {args.name} ===")
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
                           and not bool(getattr(args, "allow_ip", False))),
            allow_region=bool(getattr(args, "allow_region", False)))
        if ip.startswith("("):
            die("出口不通，先别继续")
        if mode == "check" and not _has_xvfb():
            die("headless 需要 Xvfb：sudo apt install -y xvfb")
        return _run_browser(args, proxy, cfg, mode=mode, exit_ip=ip)
    finally:
        if relay:
            relay.stop()


def _has_xvfb() -> bool:
    return shutil.which("Xvfb") is not None


def cmd_refresh(args) -> int:
    return run(args, mode="refresh")


def cmd_web(args) -> int:
    return run(args, mode="web")


def cmd_check(args) -> int:
    return run(args, mode="check")


def cmd_warmup(args) -> int:
    return run(args, mode="check" if getattr(args, "headless", False) else "warmup")
