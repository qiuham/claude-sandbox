"""DoH 客户端：经上游 SOCKS5 直连 DoH 服务器，把域名查成 IP。

只查 IP，不做系统 DNS 回退；失败就抛错。
"""

from __future__ import annotations

import ipaddress
import json
import socket
import ssl
import struct
import threading
import time

from ccs import config

BUF = 65536

# SOCKS5 地址类型：1 = IPv4，3 = 域名，4 = IPv6。
ATYP_IPV4 = 1
ATYP_DOMAIN = 3
ATYP_IPV6 = 4
IPV4_VERSION = 4


def recvn(sock, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            msg = "连接被关闭"
            raise EOFError(msg)
        buf += chunk
    return buf


def _parse_upstream(upstream: str) -> tuple[str, int]:
    addr = str(upstream).replace("socks5h://", "socks5://").replace("socks5://", "")
    host, _, port = addr.rstrip("/").rpartition(":")
    return host, int(port)


def _check_auth_reply(reply: bytes) -> None:
    if reply != b"\x05\x00":
        msg = "上游代理不接受无认证"
        raise OSError(msg)


def _check_connect_reply(head: bytes) -> None:
    if head[1] != 0:
        msg = f"上游拒绝，错误码 {head[1]}"
        raise OSError(msg)


def upstream_connect(upstream: str, host: str, port: int,
                     timeout: float) -> socket.socket:
    """连上游代理，请求它连 host:port，返回已连通的 socket。"""
    sock = socket.create_connection(_parse_upstream(upstream), timeout=timeout)
    sock.settimeout(timeout)
    try:
        sock.sendall(b"\x05\x01\x00")
        _check_auth_reply(recvn(sock, 2))
        sock.sendall(b"\x05\x01\x00" + _socks_addr(host) + struct.pack("!H", port))
        head = recvn(sock, 4)
        _check_connect_reply(head)
        _eat_bound_addr(sock, head[3])
    except Exception:
        sock.close()
        raise
    return sock


def _socks_addr(host: str) -> bytes:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return bytes([ATYP_DOMAIN]) + bytes([len(host)]) + host.encode()
    return ((bytes([ATYP_IPV4]) + ip.packed) if ip.version == IPV4_VERSION
            else (bytes([ATYP_IPV6]) + ip.packed))


def _eat_bound_addr(sock, atyp: int) -> None:
    if atyp == ATYP_IPV4:
        recvn(sock, 4)
    elif atyp == ATYP_DOMAIN:
        recvn(sock, recvn(sock, 1)[0])
    elif atyp == ATYP_IPV6:
        recvn(sock, 16)
    recvn(sock, 2)


def _dns_section(cfg: dict) -> dict:
    value = cfg.get("dns")
    return value if isinstance(value, dict) else cfg


class DohClient:
    """按 TTL 缓存域名解析结果。"""

    def __init__(self, upstream: str, cfg: dict) -> None:
        dns = _dns_section(cfg)
        defaults = config.DEFAULT_CONFIG["dns"]
        self.upstream = upstream.replace("socks5h://", "socks5://")
        self.doh_ip = str(dns.get("doh_ip") or defaults["doh_ip"])
        self.doh_name = str(dns.get("doh_name") or defaults["doh_name"])
        self.doh_path = str(dns.get("doh_path") or defaults["doh_path"])
        self.ttl_min = int(dns.get("ttl_min") or defaults["ttl_min"])
        self.ttl_max = int(dns.get("ttl_max") or defaults["ttl_max"])
        self.connect_timeout = float(dns.get("connect_timeout_s")
                                     or defaults["connect_timeout_s"])
        self._cache: dict[str, tuple[float, list[str]]] = {}
        self._lock = threading.Lock()

    def resolve(self, name: str) -> list[str]:
        """返回 IP 列表。先查缓存，缓存过期后再问 DoH。"""
        key = name.lower().rstrip(".")
        now = time.monotonic()
        with self._lock:
            hit = self._cache.get(key)
            if hit and hit[0] > now:
                return list(hit[1])

        best_ttl = self.ttl_max
        for qtype in ("A", "AAAA"):
            data = self._query(name, qtype)
            answer = data.get("Answer") or []
            ips = [a["data"] for a in answer if a.get("type") in (1, 28) and a.get("data")]
            for item in answer:
                ttl = item.get("TTL")
                if isinstance(ttl, int) and ttl > 0:
                    best_ttl = min(best_ttl, ttl)
            if ips:
                ttl = max(self.ttl_min, best_ttl)
                with self._lock:
                    self._cache[key] = (now + ttl, list(ips))
                return ips

        msg = f"没有解析结果：{name}"
        raise OSError(msg)

    def _query(self, name: str, qtype: str) -> dict:
        raw = upstream_connect(self.upstream, self.doh_ip, 443, self.connect_timeout)
        try:
            ctx = ssl.create_default_context()
            tls = ctx.wrap_socket(raw, server_hostname=self.doh_name)
        except Exception:
            raw.close()
            raise
        try:
            tls.sendall(self._request(name, qtype).encode())
            body = self._read_all(tls)
        finally:
            tls.close()
        return self._parse(body)

    def _request(self, name: str, qtype: str) -> str:
        return (
            f"GET {self.doh_path}?name={name}&type={qtype} HTTP/1.1\r\n"
            f"Host: {self.doh_name}\r\n"
            "Accept: application/dns-json\r\n"
            "Connection: close\r\n\r\n"
        )

    @staticmethod
    def _read_all(sock) -> bytes:
        chunks = []
        while True:
            data = sock.recv(BUF)
            if not data:
                break
            chunks.append(data)
        return b"".join(chunks)

    @staticmethod
    def _parse(body: bytes) -> dict:
        head, _, payload = body.partition(b"\r\n\r\n")
        status = head.split(b"\r\n")[0]
        if b" 200 " not in status:
            msg = f"DoH 返回异常：{status[:80]!r}"
            raise OSError(msg)
        return json.loads(payload)
