"""网络包：出口解析、出口 IP 预检、DoH、DNS 中继。"""

from __future__ import annotations

from ccs.network.dns_relay import DnsRelay
from ccs.network.preflight import curl_exit, preflight
from ccs.network.proxy import is_proxy_spec, normalize_socks, pick_exit, resolve_exit

__all__ = [
    "DnsRelay",
    "curl_exit",
    "is_proxy_spec",
    "normalize_socks",
    "pick_exit",
    "preflight",
    "resolve_exit",
]
