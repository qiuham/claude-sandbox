"""读出 Clash Verge 当前选中的节点名。

不连 Clash 的 API（Verge 只开 Windows 命名管道，WSL 进不去），
直接读它的 profiles.yaml：那里记着每个分组当前选了谁。

分组可以套娃（🐟 Other → 🔰 PROXY → 节点），这里顺着链走到最后一个。

"""

from __future__ import annotations

import re
from pathlib import Path

CONFIG_DIR = "io.github.clash-verge-rev.clash-verge-rev"
UID_RE = re.compile(r"^- uid:\s*(\S+)", re.MULTILINE)
CURRENT_RE = re.compile(r"^current:\s*(\S+)", re.MULTILINE)
NAME_RE = re.compile(r"^\s*- name:\s*(.+?)\s*$", re.MULTILINE)
NOW_RE = re.compile(r"^\s+now:\s*(.+?)\s*$", re.MULTILINE)


def config_dirs() -> list[Path]:
    """可能的位置：Linux 原生、WSL 里各 Windows 用户的 AppData。"""
    found = [Path.home() / ".config" / CONFIG_DIR]
    users = Path("/mnt/c/Users")
    if users.is_dir():
        found.extend(users.glob(f"*/AppData/Roaming/{CONFIG_DIR}"))
    return [p for p in found if (p / "profiles.yaml").is_file()]


def _unquote(value: str) -> str:
    return value.strip().strip("'\"")


def _section(text: str, uid: str) -> str:
    """取出某个 profile 的那一段（从它的 - uid 到下一个 - uid）。"""
    starts = [(m.start(), m.group(1)) for m in UID_RE.finditer(text)]
    for index, (pos, found_uid) in enumerate(starts):
        if found_uid != uid:
            continue
        end = starts[index + 1][0] if index + 1 < len(starts) else len(text)
        return text[pos:end]
    return ""


def selected_pairs(section: str) -> dict[str, str]:
    """selected 里的 分组名 → 当前选中项，按出现顺序。"""
    pairs: dict[str, str] = {}
    names = NAME_RE.findall(section)
    nows = NOW_RE.findall(section)
    for name, now in zip(names, nows, strict=False):
        pairs[_unquote(name)] = _unquote(now)
    return pairs


def _resolve(pairs: dict[str, str]) -> str:
    """顺着分组链走到最后一个名字。找不到根就按第一个分组走。"""
    referenced = set(pairs.values())
    roots = [name for name in pairs if name not in referenced]
    current = roots[0] if roots else next(iter(pairs), "")
    seen: list[str] = []
    while current in pairs and current not in seen:
        seen.append(current)
        current = pairs[current]
    return current


def current_node() -> str:
    """当前节点名；不是 Clash Verge、或者读不出来就返回空串。"""
    for folder in config_dirs():
        try:
            text = (folder / "profiles.yaml").read_text(encoding="utf-8")
        except OSError:
            continue
        match = CURRENT_RE.search(text)
        if not match:
            continue
        pairs = selected_pairs(_section(text, match.group(1)))
        if pairs:
            return _resolve(pairs)
    return ""


def label(node: str) -> str:
    """显示用：有节点名就加括号，没有就空串。"""
    return f"（{node}）" if node else ""
