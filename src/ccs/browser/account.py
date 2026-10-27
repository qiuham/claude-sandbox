"""账号信息：从页面抓取，写进 account.json。"""

from __future__ import annotations

import time
from contextlib import suppress

from ccs import ui
from ccs.browser.js import HARVEST_JS
from ccs.errors import log_line
from ccs.sandbox import store


def harvest_account(page, name: str) -> dict | None:
    """在 claude.ai 页面里抓账号信息。

    必须在浏览器里做：Cloudflare 会拦掉浏览器之外的请求（实测 curl 一律
    返回 "Just a moment..."），所以不能用外部 HTTP 拿。
    """
    try:
        data = page.evaluate(HARVEST_JS) or {}
    except Exception as exc:  # noqa: BLE001 - 页面脚本失败只能记日志，抓不到账号不算致命
        log_line(f"抓取账号失败: {type(exc).__name__}: {exc}")
        return None

    acct = data.get("acct") or {}
    orgs = data.get("orgs") or []
    if not acct and not orgs:
        return None                      # 没登录

    cookies = {}
    with suppress(Exception):
        for c in page.context.cookies("https://claude.ai"):
            cookies[c.get("name", "")] = c.get("value", "")

    active = cookies.get("lastActiveOrg", "")
    org = next((o for o in orgs if o.get("uuid") == active), orgs[0] if orgs else {})
    info = {
        "email": acct.get("email_address") or "",
        "display_name": acct.get("display_name") or "",
        "account_created_at": acct.get("created_at") or "",
        "org_id": org.get("uuid") or active,
        "org_name": org.get("name") or "",
        "org_created_at": org.get("created_at") or "",
        "plan": org.get("rate_limit_tier") or org.get("analytics_subscription_plan") or "",
        "plan_label": org.get("plan_display_name") or org.get("plan_display_label") or "",
        "billing_type": org.get("billing_type") or "",
        "session_key": cookies.get("sessionKey", ""),
        "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    merged = {**store.read_account(name), **info}   # 保留对话统计、存活记录等字段
    if not store.write_account(name, merged):
        log_line("写 account.json 失败")
        return None
    return info


def say_account(page, name: str) -> None:
    if harvest_account(page, name):
        ui.emit_note("账号", store.read_account(name).get("email") or "已记录")
