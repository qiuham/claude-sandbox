"""按出口 IP 查地理位置，用来在报表里认出“这是哪条线”。

节点名只有 Clash Verge 能读到；地理位置对任何代理都成立。
结果缓存到 ~/.cache/claude-sandbox/geo.json，7 天内不再查。

"""

from __future__ import annotations

import json
import time
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

CACHE_TTL_S = 7 * 24 * 3600
CACHE_VERSION = 3
FIELDS = "status,country,city,isp"
API = "http://ip-api.com/json/{ip}?fields={fields}&lang=zh-CN"


def cache_file() -> Path:
    return Path.home() / ".cache" / "claude-sandbox" / "geo.json"


def _load_cache() -> dict:
    try:
        data = json.loads(cache_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_cache(data: dict) -> None:
    try:
        path = cache_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _fetch(ip: str, timeout_s: float) -> str:
    url = API.format(ip=quote(ip), fields=FIELDS)
    try:
        with urlopen(url, timeout=timeout_s) as resp:  # noqa: S310 - ip-api 的免费接口只开 http
            data = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError):
        return ""
    if data.get("status") != "success":
        return ""
    place = " ".join(x for x in (data.get("country"), data.get("city")) if x)
    return " · ".join(x for x in (place, data.get("isp")) if x)


def lookup(ip: str, *, timeout_s: float = 1.5) -> str:
    """“美国 圣何塞 · Wave Broadband”。查不到返回空串。"""
    if not ip or ip.startswith("("):
        return ""
    cache = _load_cache()
    item = cache.get(ip)
    if (isinstance(item, dict) and item.get("v") == CACHE_VERSION
            and time.time() - float(item.get("ts") or 0) < CACHE_TTL_S):
        return str(item.get("label") or "")
    label = _fetch(ip, timeout_s)
    if label:
        cache[ip] = {"label": label, "ts": int(time.time()), "v": CACHE_VERSION}
        _save_cache(cache)
    return label
