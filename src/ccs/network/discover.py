"""找本机代理：用户大多已经开了 Clash / v2ray，只是没告诉 ccs。

挨个试常见端口，做一次 SOCKS5 握手，再 CONNECT 一个真实域名。
只开端口不算数，能转发才算。

"""

from __future__ import annotations

import socket
import struct
from pathlib import Path

PORTS = (7897, 7890, 10808, 1080)     # Clash Verge / Clash / v2ray / 通用
MIN_ROUTE_FIELDS = 3
SOCKS_REPLY_HEAD = 4
ATYP_IPV4 = 1
ATYP_DOMAIN = 3
ATYP_IPV6 = 4
CONNECT_TIMEOUT_S = 0.4
REPLY_TIMEOUT_S = 1.5
PROBE_HOST = "api.ipify.org"
PROBE_PORT = 443


def _default_gateway() -> str:
    """WSL 的 NAT 模式下，Windows 主机不在 127.0.0.1，而在默认网关。"""
    try:
        lines = Path("/proc/net/route").read_text(encoding="ascii").splitlines()
    except OSError:
        return ""
    for line in lines[1:]:
        fields = line.split()
        if len(fields) >= MIN_ROUTE_FIELDS and fields[1] == "00000000":
            return socket.inet_ntoa(struct.pack("<L", int(fields[2], 16)))
    return ""


def hosts() -> tuple[str, ...]:
    out = ["127.0.0.1"]
    gateway = _default_gateway()
    if gateway:
        out.append(gateway)
    return tuple(out)


def _recv(sock: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return buf


def _skip_reply_address(sock: socket.socket, atyp: int) -> None:
    if atyp == ATYP_IPV4:
        _recv(sock, 6)
    elif atyp == ATYP_DOMAIN:
        _recv(sock, _recv(sock, 1)[0] + 2)
    elif atyp == ATYP_IPV6:
        _recv(sock, 18)


def socks5_ok(host: str, port: int) -> bool:
    """能握手、能 CONNECT 到 PROBE_HOST，就算可用。"""
    try:
        with socket.create_connection((host, port), timeout=CONNECT_TIMEOUT_S) as sock:
            sock.settimeout(REPLY_TIMEOUT_S)
            sock.sendall(b"\x05\x01\x00")
            if _recv(sock, 2) != b"\x05\x00":
                return False
            name = PROBE_HOST.encode()
            sock.sendall(b"\x05\x01\x00\x03" + bytes([len(name)]) + name
                         + struct.pack("!H", PROBE_PORT))
            head = _recv(sock, 4)
            if len(head) < SOCKS_REPLY_HEAD or head[1] != 0:
                return False
            _skip_reply_address(sock, head[3])
            return True
    except OSError:
        return False


def probe_all() -> list[tuple[str, bool]]:
    """每个候选地址试一次，按顺序返回（地址, 通不通）。"""
    return [(f"socks5h://{host}:{port}", socks5_ok(host, port))
            for host in hosts() for port in PORTS]


def find_local_proxy() -> str:
    """第一个能用的地址；一个都没有就返回空串。"""
    for address, ok in probe_all():
        if ok:
            return address
    return ""
