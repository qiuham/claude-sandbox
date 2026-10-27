"""运行期环境：读配置，并把路径、窗口、进程的模块参数配好。"""

from __future__ import annotations

from ccs import config, paths
from ccs.browser import process
from ccs.errors import die


def load_runtime() -> dict:
    """读配置，并把路径、进程的模块参数配好。"""
    cfg = config.load_config()
    paths.configure(cfg, config.source_path())
    process.configure(config.config_timeouts(cfg))
    return cfg


def check_name(name: str) -> None:
    """沙盒名只能是单层目录名。"""
    if name in {".", ".."} or "/" in name or "\\" in name:
        die("沙盒名字不能带路径分隔符")
