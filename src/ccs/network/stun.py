"""一次 STUN 查询：这台机器直连 UDP 时，对端看到的 IP 是多少。

用来判断 UDP 有没有被外层代理接管。TCP 走了代理、UDP 没走时，
两个 IP 不一样——那就是 WebRTC 之类 UDP 流量的泄露口。

"""

from __future__ import annotations

import os
import socket
import struct

# Google 的 19302 在国内常被丢包，先试 Cloudflare
SERVERS = (
    ("stun.cloudflare.com", 3478),
    ("stun.nextcloud.com", 3478),
    ("stun.l.google.com", 19302),
)
MAGIC_COOKIE = 0x2112A442
HEADER_LEN = 20
BINDING_REQUEST = 0x0001
BINDING_RESPONSE = 0x0101
XOR_MAPPED_LEN = 8
XOR_MAPPED = 0x0020
MAPPED = 0x0001


def _mapped_from_attr(attr_type: int, value: bytes) -> str:
    if attr_type not in (XOR_MAPPED, MAPPED) or len(value) < XOR_MAPPED_LEN or value[1] != 1:
        return ""
    port_and_ip = bytearray(value[2:8])
    if attr_type == XOR_MAPPED:
        cookie = struct.pack("!I", MAGIC_COOKIE)
        port_and_ip[0] ^= cookie[0]
        port_and_ip[1] ^= cookie[1]
        for i in range(4):
            port_and_ip[2 + i] ^= cookie[i]
    return socket.inet_ntoa(bytes(port_and_ip[2:6]))


def parse(data: bytes, transaction_id: bytes) -> str:
    """从 STUN 响应里取映射 IP；不是本事务的响应返回空串。"""
    if len(data) < HEADER_LEN:
        return ""
    msg_type, length, cookie = struct.unpack("!HHI", data[:8])
    if msg_type != BINDING_RESPONSE or cookie != MAGIC_COOKIE:
        return ""
    if data[8:HEADER_LEN] != transaction_id:
        return ""
    offset = HEADER_LEN
    end = min(len(data), HEADER_LEN + length)
    while offset + 4 <= end:
        attr_type, attr_len = struct.unpack("!HH", data[offset:offset + 4])
        value = data[offset + 4:offset + 4 + attr_len]
        ip = _mapped_from_attr(attr_type, value)
        if ip:
            return ip
        offset += 4 + attr_len + (-attr_len % 4)   # 属性按 4 字节对齐
    return ""


def _ask(host: str, port: int, timeout_s: float) -> str:
    transaction_id = os.urandom(12)
    request = struct.pack("!HHI", BINDING_REQUEST, 0, MAGIC_COOKIE) + transaction_id
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.settimeout(timeout_s)
            sock.sendto(request, (host, port))
            data, _ = sock.recvfrom(2048)
    except OSError:
        return ""
    return parse(data, transaction_id)


def mapped_ip(timeout_s: float = 0.5) -> str:
    """直连发 STUN 请求，逐个服务器试。拿不到（UDP 被挡 / 没网）返回空串。"""
    for host, port in SERVERS:
        ip = _ask(host, port, timeout_s)
        if ip:
            return ip
    return ""
