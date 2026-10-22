"""沙盒的数据模型：meta.json 在内存里的样子。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Sandbox:
    """一个沙盒的元数据。

    已知字段放在具名字段里；以后多出来的键放 extra，写回时原样保留。
    """

    name: str
    exit: str = "direct"
    created_at: str = ""
    exit_ip: str | None = None
    extra: dict = field(default_factory=dict)

    @classmethod
    def from_meta(cls, data: dict) -> Sandbox:
        known = {"name", "exit", "created_at", "exit_ip"}
        exit_ip = data.get("exit_ip")
        return cls(
            name=str(data.get("name") or ""),
            exit=str(data.get("exit") or "direct"),
            created_at=str(data.get("created_at") or ""),
            exit_ip=str(exit_ip) if exit_ip else None,
            extra={k: v for k, v in data.items() if k not in known},
        )

    def to_meta(self) -> dict:
        meta = {"name": self.name, "exit": self.exit, "created_at": self.created_at}
        if self.exit_ip:
            meta["exit_ip"] = self.exit_ip
        meta.update(self.extra)
        return meta
