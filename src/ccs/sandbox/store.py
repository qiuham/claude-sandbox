"""存储：meta.json、account.json、profile/seed.json 的读写。

这里只做读写，不打印进度；找不到沙盒时用 die 报错。
"""

from __future__ import annotations

import json
import random
from typing import TYPE_CHECKING

from ccs import paths
from ccs.errors import die
from ccs.sandbox.model import Sandbox

if TYPE_CHECKING:
    from pathlib import Path


def read_meta(name: str) -> dict:
    f = paths.meta_file(name)
    if not f.is_file():
        die(f"没有这个沙盒: {name}（先 ccs new {name}）")
    return json.loads(f.read_text(encoding="utf-8"))


def write_meta(name: str, meta: dict | Sandbox) -> None:
    if isinstance(meta, Sandbox):
        meta = meta.to_meta()
    paths.meta_file(name).write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load(name: str) -> Sandbox:
    return Sandbox.from_meta(read_meta(name))


def list_sandboxes() -> list[Sandbox]:
    root = paths.root()
    if not root.is_dir():
        return []
    out = []
    for d in sorted(root.iterdir()):
        meta_file = d / paths.META_FILE_NAME
        if meta_file.is_file():
            out.append(Sandbox.from_meta(json.loads(meta_file.read_text(encoding="utf-8"))))
    return out


def read_account(name: str) -> dict:
    path = paths.account_file(name)
    if not path.is_file():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def write_account(name: str, info: dict) -> bool:
    """写 account.json，权限 0600：里面是会话密钥。"""
    path = paths.account_file(name)
    try:
        path.write_text(json.dumps(info, ensure_ascii=False, indent=2) + "\n",
                        encoding="utf-8")
        path.chmod(0o600)
    except OSError:
        return False
    return True


def seed_of(profile: Path) -> tuple[int, bool]:
    """返回 (指纹种子, 是否这次新建)。"""
    f = profile / "seed.json"
    if f.is_file():
        try:
            seed = json.loads(f.read_text(encoding="utf-8")).get("seed")
            if isinstance(seed, int):
                return seed, False
        except (OSError, json.JSONDecodeError):
            pass
    seed = random.SystemRandom().randrange(1, 2**31)
    f.write_text(json.dumps({"seed": seed}), encoding="utf-8")
    return seed, True
