"""出口：解析 exit spec，决定走代理还是 direct。"""

from __future__ import annotations

from ccs.errors import die, say
from ccs.network import discover


def is_proxy_spec(spec: str) -> bool:
    return str(spec).startswith(("socks5://", "socks5h://", "http://", "https://"))


def normalize_socks(upstream: str) -> str:
    """上游 SOCKS 地址统一用 socks5://：DoH 在本地做，不把域名交给代理。"""
    return str(upstream).strip().replace("socks5h://", "socks5://").rstrip("/")


def resolve_exit(spec: str, cfg: dict) -> tuple[str, str]:  # noqa: RET503 - 末尾 die() 一定抛 SystemExit，ruff 不跨模块推断 NoReturn
    """出口 spec → (规范化后的 spec, 代理地址)。地址为空串表示 direct。

    规则：没指定就用配置里的 default_exit；再没有就是 direct。
    """
    spec = str(spec or "").strip()
    if not spec:
        spec = str(cfg.get("exit") or "auto").strip() or "auto"
    if spec == "auto":
        found = discover.find_local_proxy()
        if not found:
            say("出口    : 没找到本机代理，走 direct（UDP 会直连，WebRTC 已关闭）")
            say("          有代理就写进配置的 exit，例如 socks5h://127.0.0.1:7897")
            return "direct", ""
        say(f"出口    : 检测到本机代理 {found}，已设为出口")
        say("          不想要就加 --exit direct")
        return found, found
    if spec == "direct":
        return "direct", ""
    if is_proxy_spec(spec):
        return spec, spec
    die(f"出口只认 direct 或代理地址（socks5h://、http://），收到 {spec!r}")


def pick_exit(args, meta: dict | None = None) -> str:
    """命令行优先，其次沙盒记录；都没有就空串（resolve_exit 会兜底成配置/direct）。"""
    for value in (getattr(args, "proxy", None), getattr(args, "exit", None)):
        if value:
            return value
    return str((meta or {}).get("exit") or "")
