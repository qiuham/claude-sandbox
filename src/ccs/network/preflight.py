"""预检：量出口 IP，和上次记录对比。

**量的是 claude.ai 看到的那个 IP**：请求 `https://claude.ai/cdn-cgi/trace`，
它和页面请求命中同一条分流规则，所以出口一致。用 ipify 之类的站点量出来的
可能是另一条规则（比如未匹配流量），会和 Claude 实际看到的出口对不上。
"""

from __future__ import annotations

import subprocess

from ccs import config, ui
from ccs.errors import die, say
from ccs.network import geo, regions
from ccs.sandbox import store


def _timeout(cfg: dict, key: str) -> float:
    defaults = config.DEFAULT_CONFIG["browser"]["timeouts"]
    return float(((cfg.get("browser") or {}).get("timeouts") or {}).get(key,
                                                                      defaults[key]))


TRACE_URL = "https://claude.ai/cdn-cgi/trace"
# trace 不可用时的后备：这些站点走的是哪条规则不由我们决定，量出来的 IP 可能和 Claude 不同。
FALLBACK_URL = "https://api.ipify.org"


def _curl(url: str, proxy: str, cfg: dict) -> str:
    cmd = ["curl", "-s", "--max-time", str(_timeout(cfg, "preflight_s"))]
    if proxy:
        # 必须 socks5h：让域名交给代理（中继）解析。
        # 用 socks5 的话 curl 会先在本机查 DNS —— 既绕过中继，也把查询暴露给本机。
        cmd += ["-x", proxy.replace("socks5://", "socks5h://")]
    cmd.append(url)
    try:
        out = subprocess.run(  # noqa: S603 - 命令和参数都是我们固定拼的，没有 shell
            cmd, capture_output=True, text=True, check=False,
            timeout=_timeout(cfg, "preflight_process_s"))
        return out.stdout.strip() or f"(失败: {out.stderr.strip()[:80]})"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"(失败: {exc})"


def trace_ip(text: str) -> str:
    """从 `/cdn-cgi/trace` 的 key=value 里取 ip。"""
    for line in text.splitlines():
        key, _, value = line.partition("=")
        if key.strip() == "ip":
            return value.strip()
    return ""


def curl_exit(proxy: str, cfg: dict) -> str:
    """量 Claude 实际看到的出口 IP。proxy 为空 = 走这台机器原生出口。"""
    ip = trace_ip(_curl(TRACE_URL, proxy, cfg))
    if ip:
        return ip
    # trace 不通（代理挡了或页面变了）：退回旧办法，但记一笔，方便排查
    say(ui.kv("注意", "claude.ai/cdn-cgi/trace 拿不到，改用第三方站点量出口（可能和 Claude 实际出口不同）"))
    return _curl(FALLBACK_URL, proxy, cfg)


def _check_region(ip: str, cfg: dict, *, allow_region: bool) -> None:
    """出口在 Claude 不支持的地区就不让启动。查不到国家码时放行。"""
    if allow_region or not cfg.get("region_check", True):
        return
    code = geo.country_code(ip)
    if not code or regions.supported(code):
        return
    label = geo.lookup(ip) or code
    die(f"出口在 {label}（{code}），Claude 不支持这个地区，已阻止启动。\n"
        "           确认要继续：加 --allow-region，或把配置里的 region_check 改成 false。")


def preflight(name: str, spec: str, proxy: str, cfg: dict,  # noqa: PLR0913 - 参数都是预检要用的
              *, die_on_change: bool = True, allow_region: bool = False) -> str:
    if proxy:
        say(ui.kv("出口", spec))
    else:
        say(ui.kv("出口", "direct（不设代理，走原生环境）"))
        say(ui.indent("出口 IP 由这台机器的 TUN/透明代理决定；沙盒不替你设代理"))
    ip = curl_exit(proxy, cfg)
    ok = not ip.startswith("(")
    label = str(cfg.get("exit_label") or "")
    if ok and not label:
        label = geo.lookup(ip)
    say(ui.kv("出口 IP", ip + (f"（{label}）" if label else "")))
    if not ok:
        return ip
    _check_region(ip, cfg, allow_region=allow_region)
    meta = store.read_meta(name)
    known = meta.get("exit_ip")
    if known and known != ip:
        if die_on_change:
            die(f"出口 IP 变了（上次 {known}，现在 {ip}）。\n           同一个号跨会话换 IP 是风险信号，已阻止启动。\n           确实要换：加 --allow-ip，或把配置里的 strict_ip 改成 false。")
        say(ui.kv("注意", f"出口 IP 变了（上次 {known}，现在 {ip}）—— 同一个号跨会话换 IP 是风险信号"))
    meta["exit_ip"] = ip
    if label:
        meta["exit_label"] = label
    store.write_meta(name, meta)
    return ip
