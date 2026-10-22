"""预检：用 curl 量出口 IP，和上次记录对比。"""

from __future__ import annotations

import subprocess

from ccs import config
from ccs.errors import die, say
from ccs.network import geo, regions
from ccs.sandbox import store


def _timeout(cfg: dict, key: str) -> float:
    defaults = config.DEFAULT_CONFIG["browser"]["timeouts"]
    return float(((cfg.get("browser") or {}).get("timeouts") or {}).get(key,
                                                                      defaults[key]))


def curl_exit(proxy: str, cfg: dict) -> str:
    """用 curl 量出口 IP。proxy 为空 = 不指定代理，量的是这台机器原生出口。"""
    cmd = ["curl", "-s", "--max-time", str(_timeout(cfg, "preflight_s"))]
    if proxy:
        cmd += ["-x", proxy]
    cmd.append("https://api.ipify.org")
    try:
        out = subprocess.run(  # noqa: S603 - 命令和参数都是我们固定拼的，没有 shell
            cmd, capture_output=True, text=True, check=False,
            timeout=_timeout(cfg, "preflight_process_s"))
        return out.stdout.strip() or f"(失败: {out.stderr.strip()[:80]})"
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"(失败: {exc})"


def _check_region(ip: str, cfg: dict, *, allow_region: bool) -> None:
    """出口在 Claude 不支持的地区就不让启动。查不到国家码时放行。"""
    if allow_region or not cfg.get("region_check", True):
        return
    code = geo.country_code(ip)
    if not code or regions.supported(code):
        return
    label = geo.lookup(ip) or code
    die(f"出口在 {label}（{code}），Claude 不支持这个地区，已阻止启动。\n"
        "       确认要继续：加 --allow-region，或把配置里的 region_check 改成 false。")


def preflight(name: str, spec: str, proxy: str, cfg: dict,  # noqa: PLR0913 - 参数都是预检要用的
              *, die_on_change: bool = True, allow_region: bool = False) -> str:
    if proxy:
        say(f"出口    : {spec}")
    else:
        say("出口    : direct（不设代理，走原生环境）")
        say("         出口 IP 由这台机器的 TUN/透明代理决定；沙盒不替你设代理")
    ip = curl_exit(proxy, cfg)
    ok = not ip.startswith("(")
    label = str(cfg.get("exit_label") or "")
    if ok and not label:
        label = geo.lookup(ip)
    say(f"出口 IP : {ip}" + (f"（{label}）" if label else ""))
    if not ok:
        return ip
    _check_region(ip, cfg, allow_region=allow_region)
    meta = store.read_meta(name)
    known = meta.get("exit_ip")
    if known and known != ip:
        if die_on_change:
            die(f"出口 IP 变了（上次 {known}，现在 {ip}）。\n       同一个号跨会话换 IP 是风险信号，已阻止启动。\n       确实要换：加 --allow-ip-change，或把配置里的 strict_ip 改成 false。")
        say(f"注意    : 出口 IP 变了（上次 {known}，现在 {ip}）—— 同一个号跨会话换 IP 是风险信号")
    meta.pop("exit_node", None)          # 旧版本记过 Clash 节点名，已经不用了
    meta["exit_ip"] = ip
    if label:
        meta["exit_label"] = label
    store.write_meta(name, meta)
    return ip
