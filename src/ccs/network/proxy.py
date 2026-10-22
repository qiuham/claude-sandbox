"""出口：解析 exit spec，决定走代理还是 direct。"""

from __future__ import annotations

from ccs.errors import die, say


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
        spec = str(cfg.get("exit") or "direct").strip() or "direct"
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
    spec = str((meta or {}).get("exit") or "")
    # 旧版本存过名字（cox 之类），现在只认 direct/地址；失效就当没记录。
    if spec and spec != "direct" and not is_proxy_spec(spec):
        say(f"注意    : 沙盒记录的出口 {spec!r} 已失效，改用默认出口")
        return ""
    return spec
