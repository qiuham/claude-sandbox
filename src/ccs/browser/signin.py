"""claude.ai 的登录页流程：填邮箱 → 提交 → 等邮件 → 打开链接 → 等登录态。

这一层只认页面和邮件，不关心"这是注册还是重新登录"，也不读配置文件。
调用方（reg.py）决定用哪个邮箱，把收信客户端和 JWT 传进来。
"""

from __future__ import annotations

import random
import re
import time
from contextlib import suppress
from typing import TYPE_CHECKING

from ccs import ui
from ccs.browser.account import harvest_account
from ccs.errors import log_line, say

if TYPE_CHECKING:
    from ccs.browser.mail import MailClient

# 落地页上的入口别名。全部锚定，避免把 "Try Claude Pro" 之类当成入口。
ENTRY_RE = r"^\s*(Sign in|Log in|Login|Try Claude|Get started|Continue with email)\s*$"
SUBMIT_RE = r"^\s*(Continue|Next|Submit|Continue with email)\s*$"

EMAIL_INPUT = 'input[type="email"], input[autocomplete="email"]'
CAPTCHA_HINT = ('iframe[src*="hcaptcha"], iframe[src*="recaptcha"], '
                'iframe[src*="challenges.cloudflare.com"], '
                '#challenge-stage, [id^="cf-challenge"], [class*="captcha"]')
# 主界面的输入框：出现说明 onboarding 已经走完
APP_INPUT = 'div[contenteditable="true"], [data-testid="chat-input"], textarea'

# 自动过的挑战一般几秒就消失；超过这个时间还在，才提示人去点。
# 这只是"什么时候提示"，不是上限。
CHALLENGE_ANNOUNCE_S = 15.0

# 各阶段的上限：卡住时不能一直不动，但也要够 Cloudflare 挑战跑完。
FORM_WAIT_S = 120.0       # 等邮箱表单出现
MAIL_WAIT_S = 300.0       # 等登录邮件
SESSION_WAIT_S = 180.0    # 开链接到拿到登录态
ONBOARDING_WAIT_S = 300.0 # 等 onboarding 走完（人操作）
PROGRESS_EVERY_S = 60.0   # 等待中每隔这么久说一句，免得看着像卡死
# 邮件到了之后，人要花几秒到半分钟看信、复制链接；立刻打开比人快太多。
MAIL_READ_MIN_S = 10.0
MAIL_READ_MAX_S = 15.0


def click_entry(page) -> bool:
    """点"登录/开始"入口。页面直接就是邮箱表单时返回 False。"""
    for role in ("button", "link"):
        try:
            locator = page.get_by_role(role, name=re.compile(ENTRY_RE, re.IGNORECASE))
            total = locator.count()      # 页面跳转中会抛，跳过这一轮
        except Exception:  # noqa: BLE001, S112 - 等页面稳下来
            continue
        for index in range(total):
            element = locator.nth(index)
            try:
                if element.is_visible():
                    element.click()
                    log_line(f"点了入口 [{role}]")
                    return True
            except Exception:  # noqa: BLE001, S112 - 换下一个候选
                continue
    return False


def wait_page_loaded(page) -> None:
    """等页面把网络请求走完（左下角不再转圈）。

    超时不报错：claude.ai 有长连接，networkidle 不一定能等到，
    真正兜底的是后面的"值对不上就接着输"。
    """
    with suppress(Exception):
        page.wait_for_load_state("load", timeout=10000)
    with suppress(Exception):
        page.wait_for_load_state("networkidle", timeout=10000)


def window_closed(page) -> bool:
    """窗口被关掉了没有。关掉就应该结束等待，而不是继续空转。"""
    try:
        return page.is_closed()
    except Exception:  # noqa: BLE001 - 查不了就当关了
        return True


def wait_progress(state: dict, label: str, deadline: float) -> str:
    """等待中每隔一分钟报一次进度；到点返回空串。"""
    now = time.time()
    if now - float(state.get("progress", 0)) < PROGRESS_EVERY_S:
        return ""
    state["progress"] = now
    left = max(0, deadline - now)
    return f"{label}（最多再等 {int(left // 60)} 分钟）"


def input_visible(page) -> bool:
    """输入框现在在不在。页面跳转中查不了，当作"还不在"。"""
    try:
        return page.locator(EMAIL_INPUT).count() > 0
    except Exception:  # noqa: BLE001 - 正在跳转
        return False


def current_value(page) -> str:
    """输入框现在的值；读不到返回空串。"""
    try:
        return page.locator(EMAIL_INPUT).first.input_value().strip()
    except Exception:  # noqa: BLE001 - 跳转中或元素没了
        return ""


def _type_email(page, email: str, timeout_s: float) -> bool:
    """点进输入框，从当前值接着输入；值不是前缀才清空重来。"""
    box = page.locator(EMAIL_INPUT).first
    box.click()                    # 内部会等元素稳定（可见、可点、位置不变）
    typed = current_value(page)
    if typed and typed != email and email.startswith(typed):
        log_line(f"输入框里已有 {len(typed)} 个字符，接着输入剩下的")
        page.keyboard.type(email[len(typed):])
    else:
        page.keyboard.press("Control+a")
        page.keyboard.press("Backspace")
        page.keyboard.type(email)
    return wait_value(page, email, timeout_s=timeout_s)


def fill_email(page, email: str, cfg: dict) -> str:
    """填邮箱并提交。返回 `ok` / `missing`（没输入框）/ `mismatch`（填进去的值不对）。"""
    timeout_s = cfg["browser"]["timeouts"].get("selector_ms", 10000) / 1000
    # Cloudflare 挑战期间页面会跳好几次，跳转中查不到元素不算失败。
    deadline = time.time() + FORM_WAIT_S
    state: dict = {}
    while time.time() < deadline:
        if window_closed(page):
            return "closed"
        if not input_visible(page):
            message = wait_progress(state, "还在等邮箱表单", deadline)
            if message:
                say(ui.kv("等待", message))
            page.wait_for_timeout(300)
            continue
        wait_page_loaded(page)          # 左下角还在转就先别打字
        if _type_email(page, email, timeout_s):
            break
        log_line("填写被页面刷新打断，重填")
    else:
        return "missing"

    for role in ("button", "link"):
        try:
            locator = page.get_by_role(role, name=re.compile(SUBMIT_RE, re.IGNORECASE))
            total = locator.count()
        except Exception:  # noqa: BLE001, S112 - 页面跳转中，换下一轮
            continue
        for index in range(total):
            element = locator.nth(index)
            try:
                if element.is_visible() and element.is_enabled():
                    element.click()
                    return "ok"
            except Exception:  # noqa: BLE001, S112 - 换下一个候选
                continue
    page.keyboard.press("Enter")      # 没有可点的提交按钮时退化成回车
    return "ok"


def wait_value(page, expected: str, *, timeout_s: float = 10) -> bool:
    """等输入框的值等于预期。引擎的打字节奏可能比调用返回慢，不能直接提交。"""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            if page.locator(EMAIL_INPUT).first.input_value().strip() == expected:
                return True
        except Exception:  # noqa: BLE001 - 输入框没了就是没填上
            return False
        page.wait_for_timeout(200)
    return False


def challenge_message(page, state: dict) -> str:
    """页面上的人机验证卡住了吗。

    Cloudflare 有自动校验（转圈），一般几秒就过；一直不消失才说明要人点。
    """
    try:
        present = page.locator(CAPTCHA_HINT).count() > 0
    except Exception:  # noqa: BLE001 - 页面跳转中查不了
        return ""
    if not present:
        state.clear()
        return ""
    state.setdefault("since", time.time())
    if state.get("announced"):
        return ""
    if time.time() - state["since"] < CHALLENGE_ANNOUNCE_S:
        return ""
    state["announced"] = True
    return "人机验证还在转：自动过的会自己消失；如果要点击，请在窗口里完成"


def wait_for_link(page, client: MailClient, jwt: str,
                  *, since_ts: float = 0, tick=None) -> str:
    """等邮件。返回链接、`code:123456`（邮件给的是验证码）或 `closed`（窗口被关）。

    等的时候顺便看一眼页面：弹验证码就提示人去窗口里做。
    """
    deadline = time.time() + MAIL_WAIT_S
    state: dict = {}
    while time.time() < deadline:
        if window_closed(page):
            return "closed"
        if tick is not None:
            tick(page)
        message = challenge_message(page, state)
        if message:
            say(ui.kv("人机验证", message))
        message = wait_progress(state, "还在等登录邮件", deadline)
        if message:
            say(ui.kv("等待", message))
        link, code = client.wait_for_login_link(
            jwt, timeout_s=3, interval_s=3, since_ts=since_ts)
        if link:
            return link
        if code:
            return f"code:{code}"
    return ""


def wait_logged_in(page, name: str, tick=None) -> bool:
    """等 sessionKey 出现；拿到就把账号信息记下来。"""
    deadline = time.time() + SESSION_WAIT_S
    state: dict = {}
    while time.time() < deadline:
        if window_closed(page):
            return False
        if tick is not None:
            tick(page)
        message = challenge_message(page, state)
        if message:
            say(ui.kv("人机验证", message))
        message = wait_progress(state, "还在等登录态", deadline)
        if message:
            say(ui.kv("等待", message))
        try:
            cookies = {c.get("name"): c.get("value")
                       for c in page.context.cookies("https://claude.ai")}
        except Exception:  # noqa: BLE001 - 跳转中读不到，下一轮再读
            page.wait_for_timeout(1000)
            continue
        if cookies.get("sessionKey"):
            harvest_account(page, name)
            return True
        page.wait_for_timeout(2000)
    return False



def sign_in(page, *, name: str, email: str, client: MailClient,  # noqa: PLR0911, PLR0913 - 每个状态都要单独返回
            jwt: str, cfg: dict, tick=None) -> str:
    """走完整段登录。返回状态：

    ok          登录成功，账号信息已记录
    no_input    页面上找不到邮箱输入框
    bad_email   填进去的邮箱和预期不一致（可能没输完）
    no_mail     5 分钟内没收到登录邮件
    code_only   邮件给的是 6 位验证码，需要人在窗口里填
    no_session  链接打开了但没拿到 sessionKey
    onboarding  已登录，但 10 分钟内没走完 onboarding
    """
    try:
        page.goto("https://claude.ai/", wait_until="domcontentloaded",
                  timeout=cfg["browser"]["timeouts"].get("goto_ms", 60000))
    except Exception as exc:  # noqa: BLE001 - Cloudflare 挑战会挡住 load，页面其实能用
        log_line(f"打开 claude.ai 超时（{type(exc).__name__}），继续看页面")
    log_line(f"入口点击: {click_entry(page)}")
    submitted_at = time.time()             # 只认这个时间之后到的邮件
    filled = fill_email(page, email, cfg)
    if filled == "closed":
        return "closed"
    if filled != "ok":
        return "no_input" if filled == "missing" else "bad_email"
    say(ui.kv("已提交", "等收信里的登录链接（最多 5 分钟）"))

    got = wait_for_link(page, client, jwt, since_ts=submitted_at, tick=tick)
    if got == "closed":
        return "closed"
    if not got:
        return "no_mail"
    if got.startswith("code:"):
        return "code_only"

    # 人不会马上点：看邮件、复制链接要几秒到半分钟
    pause = random.uniform(MAIL_READ_MIN_S, MAIL_READ_MAX_S)  # noqa: S311 - 只做节奏抖动
    log_line(f"邮件到了，{pause:.0f} 秒后打开链接")
    page.wait_for_timeout(int(pause * 1000))
    # 在当前页打开登录链接（CF 挑战会挡住 load 事件，超时不中断）
    try:
        page.goto(got, wait_until="domcontentloaded", timeout=120000)
    except Exception as exc:  # noqa: BLE001 - 挑战页的 load 事件可能永远不触发
        log_line(f"打开登录链接超时（{type(exc).__name__}），继续等登录态")
    if not wait_logged_in(page, name, tick=tick):
        return "no_session"
    say(ui.kv("已登录", "开始自动走 onboarding；遇到不认识的页面会停下"))
    # 局部导入：onboarding 依赖本模块的常量，顶层导入会成环
    from ccs.browser.onboarding import complete as complete_onboarding  # noqa: PLC0415
    result = complete_onboarding(page, name, timeout=ONBOARDING_WAIT_S, tick=tick)
    if result == "ok":
        harvest_account(page, name)
        return "ok"
    return "closed" if result == "closed" else "onboarding"
