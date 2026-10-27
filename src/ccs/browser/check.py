"""无头自检：出口 IP 和 WebRTC。"""

from __future__ import annotations

import json

from ccs import ui
from ccs.browser.js import WEBRTC_JS
from ccs.browser.webrtc import webrtc_verdict
from ccs.config import config_timeouts
from ccs.network.preflight import TRACE_URL, trace_ip


def headless_check(page, cfg: dict, exit_ip: str) -> int:
    # 问 claude.ai 自己看到的是哪个 IP：它和页面请求走同一条分流
    page_ip = ""
    try:
        page.goto(TRACE_URL, timeout=config_timeouts(cfg)["goto_ms"])
        page_ip = trace_ip(page.inner_text("body")) or page.inner_text("body").strip()[:40]
    except Exception as exc:  # noqa: BLE001 - 拿不到就只报预检那个值
        page_ip = f"(失败: {type(exc).__name__})"
    pairs = [("Claude 看到", page_ip or "-")]
    if exit_ip and page_ip and exit_ip != page_ip:
        pairs.append(("预检", exit_ip))       # 两条路不一致时都列出来
    try:
        page.goto("https://www.cloudflare.com/", timeout=config_timeouts(cfg)["goto_ms"])
        candidates = page.evaluate(WEBRTC_JS)
        pairs.append(("WebRTC 结论", webrtc_verdict(candidates, exit_ip)))
        ui.emit_pairs(pairs, fallback=[ui.kv(k, v) for k, v in pairs])
        ui.emit_note("WebRTC 候选", json.dumps(candidates, ensure_ascii=False))
    except Exception as exc:  # noqa: BLE001 - WebRTC 检测失败只提示，不影响自检退出
        pairs.append(("WebRTC", f"检测失败: {exc}"))
        ui.emit_pairs(pairs, fallback=[ui.kv(k, v) for k, v in pairs])
    return 0
