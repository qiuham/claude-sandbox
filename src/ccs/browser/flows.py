"""页面流程：刷新会话（以后注册也放这里）。"""

from __future__ import annotations

import time

from ccs import ui
from ccs.browser.account import harvest_account
from ccs.errors import log_line
from ccs.sandbox import store

REFRESH_TEXT = {
    "ok": "会话正常，账号信息已刷新",
    "invalid": "会话已失效（要重新登录）",
    "restricted": "账号受限（落在 /restricted）",
}


def refresh_flow(page, name: str) -> int:
    """探测会话是否还活着；活着就顺手把账号信息刷新一遍。"""
    url = str(getattr(page, "url", "") or "")
    if "/restricted" in url:
        state = "restricted"
    elif "/login" in url or "/signin" in url:
        state = "invalid"
    else:
        state = "ok"

    if state == "ok":
        harvest_account(page, name)

    account = store.read_account(name)
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    account["alive_state"] = state
    account["alive_checked_at"] = now
    if state == "ok" and not account.get("alive_first_ok_at"):
        account["alive_first_ok_at"] = now
    if state != "ok" and not account.get("alive_first_invalid_at"):
        account["alive_first_invalid_at"] = now
    store.write_account(name, account)

    pairs = [("状态", REFRESH_TEXT[state]), ("检测时间", str(now))]
    if account.get("alive_first_ok_at"):
        pairs.append(("首次正常", str(account["alive_first_ok_at"])))
    if account.get("alive_first_invalid_at"):
        pairs.append(("首次失效", str(account["alive_first_invalid_at"])))
    ui.emit_pairs(pairs, fallback=[ui.kv(k, v) for k, v in pairs])
    log_line(f"刷新结果: {state} · {account.get('email', '')}")
    return 0 if state == "ok" else 1
