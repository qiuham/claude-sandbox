"""提示词：从文件、目录或配置里取，挑一句没最近用过的。"""

from __future__ import annotations

import csv
import io
import random
import time
from pathlib import Path

from ccs import config
from ccs.sandbox import store


def parse_csv(text: str) -> list[str]:
    """CSV：取第一列；首行是表头（prompt/text/内容）时跳过。

    用标准库 csv 解析，所以带引号的英文逗号、嵌入换行都能处理。
    """
    rows = [row for row in csv.reader(io.StringIO(text)) if row and row[0].strip()]
    if not rows:
        return []
    first = rows[0][0].strip().lower()
    if first in ("prompt", "text", "content", "内容", "提示词"):
        rows = rows[1:]
    return [row[0].strip() for row in rows if row[0].strip()]


def parse_prompt_file(text: str) -> list[str]:
    """解析话术文件。

    - 有单独一行 `---`：按这个分隔成多段，段内可以多行（适合较长的提示词）
    - 没有分隔线：一行一句
    - `#` 开头的行和空行忽略
    """
    lines = text.splitlines()
    if any(line.strip() == "---" for line in lines):
        blocks, current = [], []
        for line in lines:
            if line.strip() == "---":
                block = "\n".join(current).strip()
                if block:
                    blocks.append(block)
                current = []
            elif not line.strip().startswith("#"):
                current.append(line)
        block = "\n".join(current).strip()
        if block:
            blocks.append(block)
        return blocks

    return [line.strip() for line in lines
            if line.strip() and not line.strip().startswith("#")]


def strip_frontmatter(text: str) -> str:
    """去掉文件开头的 YAML frontmatter（--- 包起来那段）。"""
    stripped = text.lstrip()
    if not stripped.startswith("---"):
        return text
    rest = stripped[3:]
    end = rest.find("\n---")
    if end < 0:
        return text
    return rest[end + 4:].lstrip("\n")


def read_prompt_dir(folder: Path) -> list[str]:
    """一个目录，一个文件一句提示词；按文件名排序。"""
    prompts = []
    for file in sorted(list(folder.glob("*.md")) + list(folder.glob("*.txt"))):
        try:
            body = strip_frontmatter(file.read_text(encoding="utf-8")).strip()
        except OSError:
            continue
        if body:
            prompts.append(body)
    return prompts


def load_prompts(cfg: dict) -> list[str]:
    """话术来源：先看 prompts_file（一行一句），没有就用配置里的列表。"""
    path = str(cfg["warmup"].get("prompts_file") or "").strip()
    if path:
        file = Path(path).expanduser()
        if file.is_file():
            raw = file.read_text(encoding="utf-8")
            blocks = parse_csv(raw) if file.suffix.lower() == ".csv" else parse_prompt_file(raw)
            if blocks:
                return blocks
    folder = str(cfg["warmup"].get("prompts_dir") or "").strip()
    if folder:
        directory = Path(folder).expanduser()
        if directory.is_dir():
            blocks = read_prompt_dir(directory)
            if blocks:
                return blocks
    return cfg["warmup"]["prompts"] or config.WARM_PROMPTS


def pick_prompt(name: str, prompts: list[str], cfg: dict) -> str:
    """挑一句：daily 按日期轮换，random 随机但不连着用同一句。"""
    if not prompts:
        return ""
    mode = str(cfg["warmup"].get("pick") or "random").lower()
    last = str(store.read_account(name).get("last_warmup_prompt") or "")

    if mode == "daily":
        index = time.localtime().tm_yday % len(prompts)
        candidate = prompts[index]
        if candidate == last and len(prompts) > 1:
            candidate = prompts[(index + 1) % len(prompts)]
        return candidate

    candidates = [p for p in prompts if p != last] or prompts
    return random.choice(candidates)  # noqa: S311 - 只挑提示词，不用于安全用途


def remember_prompt(name: str, prompt: str) -> None:
    """记下这次用了哪句，避免下次重复。"""
    account = store.read_account(name)
    if not account:
        return
    account["last_warmup_prompt"] = prompt
    account["last_warmup_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    account["warmup_count"] = int(account.get("warmup_count") or 0) + 1
    store.write_account(name, account)
