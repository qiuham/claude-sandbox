"""浏览器偏好：从配置生成 fingerprint pin 和字体清单。"""

from __future__ import annotations

from ccs import config
from ccs.errors import die


def western_font_manifest(cfg: dict) -> str:
    """引擎自带的字体清单，去掉中文字体那一批。"""
    try:
        from invisible_core._fpforge.profile import FONT_MANIFEST  # noqa: PLC0415 - 延迟导入，避免没装引擎时连 CLI 都起不来
    except ImportError:
        return ""
    markers = cfg.get("fonts_markers") or config.CJK_FONT_MARKERS
    kept = [line for line in FONT_MANIFEST.splitlines()
            if not any(m.lower() in line.lower() for m in markers)]
    return "\n".join(kept) + "\n"


def build_pin(cfg: dict, overrides: dict | None = None,
              fonts_mode: str | None = None) -> dict:
    pin = config.parse_screen(cfg.get("screen"))
    dpr = cfg.get("dpr")
    if dpr not in (None, ""):
        pin["screen.dpr"] = config.parse_dpr(dpr)
    scheme = str(cfg.get("color_scheme") or "dark").strip().lower()
    if scheme == "light":
        pin["dark_theme"] = False
    elif scheme != "auto":
        pin["dark_theme"] = True
    mode = str(fonts_mode or cfg.get("fonts") or "western").lower()
    if mode != "all":
        manifest = western_font_manifest(cfg)
        if manifest:
            pin["font.manifest"] = manifest
    if overrides:
        pin.update({k: v for k, v in overrides.items() if v not in (None, "")})
    return pin


def engine_class():
    """延迟导入浏览器引擎：只有真的启动浏览器时才需要它。"""
    try:
        from invisible_playwright import InvisiblePlaywright  # noqa: PLC0415 - 延迟导入，避免没装引擎时连 CLI 都起不来
    except ImportError:
        die("没装 invisible_playwright：在本仓库目录执行 .venv/bin/pip install -r requirements.txt")
    return InvisiblePlaywright
