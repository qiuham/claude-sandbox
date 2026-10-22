"""预检：用 curl 量出口 IP，和上次记录对比。"""

from __future__ import annotations

import subprocess

from ccs import config
from ccs.errors import die, say
from ccs.network import clash, geo
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


def preflight(name: str, spec: str, proxy: str, cfg: dict,
              *, die_on_change: bool = True) -> str:
    if proxy:
        say(f"出口    : {spec}")
    else:
        say("出口    : direct（不设代理，走原生环境）")
        say("         出口 IP 由这台机器的 TUN/透明代理决定；沙盒不替你设代理")
    ip = curl_exit(proxy, cfg)
    ok = not ip.startswith("(")
    node = clash.current_node() if proxy else ""
    label = str(cfg.get("exit_label") or "")
    if ok and not label:
        label = geo.describe(ip, node)
    say(f"出口 IP : {ip}" + (f"（{label}）" if label else ""))
    if not ok:
        return ip
    meta = store.read_meta(name)
    known = meta.get("exit_ip")
    if known and known != ip:
        if die_on_change:
            die(f"出口 IP 变了（上次 {known}，现在 {ip}）。\n       同一个号跨会话换 IP 是风险信号，已阻止启动。\n       确实要换：加 --allow-ip-change，或把配置里的 strict_ip 改成 false。")
        say(f"注意    : 出口 IP 变了（上次 {known}，现在 {ip}）—— 同一个号跨会话换 IP 是风险信号")
    meta["exit_ip"] = ip
    if node:
        meta["exit_node"] = node
    if label:
        meta["exit_label"] = label
    store.write_meta(name, meta)
    return ip
