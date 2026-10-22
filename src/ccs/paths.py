"""路径：仓库、venv、沙盒根目录、配置、日志、meta.json、account.json。

所有路径集中在这里，其他模块不自己拼目录。
"""

from __future__ import annotations

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# 进程级路径：启动时由 configure() 设置一次，之后全局共用。
ROOT = Path("~/sandboxes").expanduser().resolve()
DEFAULT_CONFIG_FILE = Path("~/.config/claude-sandbox/ccs.toml").expanduser()
CONFIG_FILE = DEFAULT_CONFIG_FILE

PROFILE_DIR_NAME = "profile"
META_FILE_NAME = "meta.json"
ACCOUNT_FILE_NAME = "account.json"
LOG_FILE_NAME = "ccs.log"


def configure(cfg: dict, config_file: Path | None = None) -> None:
    """按配置和环境变量设置全局路径。"""
    global ROOT, CONFIG_FILE  # noqa: PLW0603 - 进程级路径，启动时设置一次
    global PROFILE_DIR_NAME, META_FILE_NAME, ACCOUNT_FILE_NAME, LOG_FILE_NAME  # noqa: PLW0603 - 同上

    p = cfg.get("paths") or {}
    root = os.environ.get("SANDBOX_ROOT") or p.get("root") or "~/sandboxes"
    ROOT = Path(str(root)).expanduser().resolve()

    if config_file is None:
        raw = os.environ.get("SANDBOX_CONFIG")
        config_file = Path(raw).expanduser() if raw else DEFAULT_CONFIG_FILE
    CONFIG_FILE = Path(config_file)

    PROFILE_DIR_NAME = str(p.get("profile_dir") or "profile")
    META_FILE_NAME = str(p.get("meta_file") or "meta.json")
    ACCOUNT_FILE_NAME = str(p.get("account_file") or "account.json")
    LOG_FILE_NAME = str(p.get("log_file") or "ccs.log")


def repo_root() -> Path:
    return REPO_ROOT


def venv_python() -> Path:
    return REPO_ROOT / ".venv" / "bin" / "python3"


def root() -> Path:
    return ROOT


def config_file() -> Path:
    return CONFIG_FILE


def sandbox_dir(name: str) -> Path:
    return ROOT / name


def profile_dir(name: str) -> Path:
    return sandbox_dir(name) / PROFILE_DIR_NAME


def meta_file(name: str) -> Path:
    return sandbox_dir(name) / META_FILE_NAME


def account_file(name: str) -> Path:
    return sandbox_dir(name) / ACCOUNT_FILE_NAME


def log_file(name: str) -> Path:
    return sandbox_dir(name) / LOG_FILE_NAME
