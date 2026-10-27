"""WebRTC 候选分析：有没有泄露真实 IP。"""

from __future__ import annotations

import ipaddress


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
