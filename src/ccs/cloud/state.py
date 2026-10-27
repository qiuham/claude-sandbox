"""云端的本地状态：token 存哪、每个沙盒同步到哪一版。

两处：
  ~/.config/claude-sandbox/cloud.toml   服务地址 + token，权限 0600
  ~/sandboxes/<名字>/cloud.json         这个沙盒上次同步的版本号和内容哈希
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from ccs import paths

CONFIG_FILE = Path("~/.config/claude-sandbox/cloud.toml").expanduser()
STATE_FILE_NAME = "cloud.json"


def load() -> dict:
    """{"url": 服务地址, "token": 令牌}；没登录过就是空串。"""
    if not CONFIG_FILE.is_file():
        return {"url": "", "token": ""}
    try:
        data = tomllib.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return {"url": "", "token": ""}
    cloud = data.get("cloud") or {}
    return {"url": str(cloud.get("url") or ""),
            "token": str(cloud.get("token") or "")}


def save(url: str, token: str) -> bool:
    lines = ["[cloud]", f"url = {json.dumps(url.rstrip('/'))}",
             f"token = {json.dumps(token)}"]
    try:
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
        CONFIG_FILE.chmod(0o600)
    except OSError:
        return False
    return True


def state_file(name: str) -> Path:
    return paths.sandbox_dir(name) / STATE_FILE_NAME


def read_state(name: str) -> dict:
    path = state_file(name)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def write_state(name: str, data: dict) -> bool:
    try:
        state_file(name).write_text(
            json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError:
        return False
    return True


def mail_of(name: str) -> str:
    from ccs.sandbox import store  # noqa: PLC0415 - 避免和 store 互相导入

    return str(store.read_account(name).get("email") or "").lower()


def local_for(email: str, url: str) -> str:
    """哪个沙盒对应这个邮箱。没有就返回空串。"""
    from ccs.sandbox import store  # noqa: PLC0415 - 同上

    target = email.lower()
    for sandbox in store.list_sandboxes():
        state = read_state(sandbox.name)
        if str(state.get("email") or "").lower() == target \
                and str(state.get("url") or "").rstrip("/") == url.rstrip("/"):
            return sandbox.name
    return ""


def sandbox_for(email: str, url: str) -> str:
    """先按 cloud.json 找；没同步过的沙盒按 account.json 里的邮箱找。"""
    found = local_for(email, url)
    if found:
        return found
    from ccs.sandbox import store  # noqa: PLC0415 - 同上

    target = email.lower()
    for sandbox in store.list_sandboxes():
        if mail_of(sandbox.name) == target:
            return sandbox.name
    return ""
