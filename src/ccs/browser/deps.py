"""引擎启动需要的系统库。

Firefox 是动态链接的。Ubuntu / WSL 的精简安装常缺 libasound 这类库，
缺了引擎会直接退出（exit code 255），报 "Couldn't load XPCOM"。
这里按 soname 挨个试着加载，缺哪个就告诉用户装哪个包。

"""

from __future__ import annotations

import ctypes
import subprocess

# (soname, Debian/Ubuntu 包名候选)。24.04 起很多包带 t64 后缀，
# 22.04 还是旧名，所以每个都留两个候选，实际用 apt-cache 能查到的那一个。
LIBS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("libasound.so.2", ("libasound2t64", "libasound2")),
    ("libdbus-1.so.3", ("libdbus-1-3",)),
    ("libgtk-3.so.0", ("libgtk-3-0t64", "libgtk-3-0")),
    ("libatk-1.0.so.0", ("libatk1.0-0t64", "libatk1.0-0")),
    ("libatk-bridge-2.0.so.0", ("libatk-bridge2.0-0t64", "libatk-bridge2.0-0")),
    ("libatspi.so.0", ("libatspi2.0-0t64", "libatspi2.0-0")),
    ("libxkbcommon.so.0", ("libxkbcommon0",)),
    ("libpango-1.0.so.0", ("libpango-1.0-0",)),
    ("libcairo.so.2", ("libcairo2",)),
    ("libgdk_pixbuf-2.0.so.0", ("libgdk-pixbuf-2.0-0",)),
    ("libnss3.so", ("libnss3",)),
    ("libnspr4.so", ("libnspr4",)),
    ("libglib-2.0.so.0", ("libglib2.0-0t64", "libglib2.0-0")),
    ("libexpat.so.1", ("libexpat1",)),
    ("libX11.so.6", ("libx11-6",)),
    ("libX11-xcb.so.1", ("libx11-xcb1",)),
    ("libXcomposite.so.1", ("libxcomposite1",)),
    ("libXdamage.so.1", ("libxdamage1",)),
    ("libXfixes.so.3", ("libxfixes3",)),
    ("libXrandr.so.2", ("libxrandr2",)),
    ("libxcb.so.1", ("libxcb1",)),
)


def loadable(soname: str) -> bool:
    """能不能直接加载这个库。只加载，不调用里面的函数。"""
    try:
        ctypes.CDLL(soname)
    except OSError:
        return False
    return True


def missing() -> list[str]:
    return [soname for soname, _ in LIBS if not loadable(soname)]


def _apt_has(pkg: str) -> bool:
    try:
        result = subprocess.run(  # noqa: S603 - 包名来自本文件的常量
            ["apt-cache", "show", pkg],  # noqa: S607 - 用 PATH 里的 apt-cache
            capture_output=True, check=False, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def packages(names: list[str] | None = None) -> list[str]:
    """把缺的 soname 换成这机器上能装的包名。"""
    picked: list[str] = []
    for soname, candidates in LIBS:
        if names is not None and soname not in names:
            continue
        choice = next((c for c in candidates if _apt_has(c)), candidates[0])
        if choice not in picked:
            picked.append(choice)
    return picked


def hint() -> str:
    """缺库时给用户看的一句话。没缺就返回空串。"""
    names = missing()
    if not names:
        return ""
    pkgs = packages(names)
    return (f"缺少系统库：{', '.join(names)}\n"
            f"  Ubuntu / WSL 装一下：sudo apt install -y {' '.join(pkgs)}")
