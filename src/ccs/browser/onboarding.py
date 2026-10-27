"""新手引导：先认页面，再动手。

页面写什么语言取决于出口，所以一个字都不看，只认结构。

**流程**：每到一个页面，先分类，再做这一类页面该做的事。

| 类型 | 判断依据（按优先级） | 动作 |
|---|---|---|
| 升级页 | 地址含 upgrade / checkout / billing | 点免费继续（不点购买） |
| 同意页 | 有勾选框 | 从下往上勾，勾到主按钮可用；然后提交 |
| 表单页 | 有空着的文字输入框 | 填沙盒名；然后提交 |
| 选项页 | 下拉列表已经展开 | 选第一个没选过的，然后合上列表 |
| 选择页 | 有整块卡片（w-full，且不是 CTA） | 点第一张卡片 |
| 下拉页 | 有 combobox，列表没展开 | 点开 |
| 提示页 | 有次要按钮（ghost） | 点次要按钮（跳过） |
| 继续页 | 有主按钮（group/btn，非 ghost、非购买） | 点它 |
| 不认识 | 以上都不匹配 | 停下，交给人 |

两条硬禁令，任何页面都不例外：

- `data-cta`：购买按钮（Get Max plan / 免费方案也是它，一律不点）
- `.cds-text-link`：文字链接（"download manually" 这类）

每一步只做一个动作，做完停一会儿，和人的节奏一样。
"""

from __future__ import annotations

import random
import re
import time
from contextlib import suppress

from ccs.browser.signin import APP_INPUT, window_closed
from ccs.errors import log_line

ACTION_TIMEOUT_MS = 8000
# 同一页里的连续操作：和录制里的 1～2 秒对齐
STEP_PAUSE_MIN_MS = 800
STEP_PAUSE_MAX_MS = 2000
# 换了新页面之后：人要看页面、做判断，录制里是 5～21 秒
PAGE_PAUSE_MIN_MS = 3000
PAGE_PAUSE_MAX_MS = 10000
# 点完之后等页面自己跳一下，别把"正在跳转"当成没进展
SETTLE_S = 2.5
# 不认识的页面：试几次就停
UNKNOWN_LIMIT = 3
# 认识的页面一直没进展：先 Esc，再回聊天页，最后停下
ESC_AFTER_STALL = 4
JUMP_AFTER_STALL = 6
STUCK_LIMIT = 10

# 两条硬禁令，任何页面都不例外
FORBIDDEN = "[data-cta], .cds-text-link"

UPGRADE_URL_RE = re.compile(r"/(upgrade|checkout|billing|subscribe)", re.IGNORECASE)
CHECKBOX_SEL = '[role="checkbox"], input[type="checkbox"]'
TEXT_INPUT_SEL = 'input[type="text"]:not([readonly])'
COMBO_SEL = '[role="combobox"], select'
OPTION_SEL = '[role="option"], [role="radio"]'
GHOST_SEL = "button[data-cds-ghost], [role=button][data-cds-ghost]"
BUTTON_SEL = "button"
CARD_SEL = "button, [role=button]"


# ---------------------------------------------------------------- 基础操作


def _visible(element) -> bool:
    try:
        return element.is_visible()
    except Exception:  # noqa: BLE001 - 元素没了
        return False


def _safe(element) -> bool:
    """这个元素或它的祖先是不是禁令里的东西。"""
    try:
        return not element.evaluate("(el, sel) => !!(el.closest(sel))", FORBIDDEN)
    except Exception:  # noqa: BLE001 - 查不了就按不安全处理
        return False


def _click(element) -> bool:
    if not _safe(element):
        log_line("跳过禁用元素（购买按钮/下载链接）")
        return False
    try:
        element.click(timeout=ACTION_TIMEOUT_MS)   # 引擎负责接近路径和偏心落点
    except Exception:  # noqa: BLE001 - 元素消失或被挡住
        return False
    return True


def _first_visible(locator, limit: int = 8):
    try:
        total = locator.count()
    except Exception:  # noqa: BLE001 - 页面跳转中
        return None
    for index in range(min(total, limit)):
        element = locator.nth(index)
        if _visible(element):
            return element
    return None


def _attr(element, name: str) -> str:
    try:
        return element.get_attribute(name) or ""
    except Exception:  # noqa: BLE001 - 元素没了
        return ""


def _checked(element) -> bool:
    try:
        return bool(element.evaluate(
            "(el) => el.getAttribute('aria-checked') === 'true'"
            " || el.getAttribute('data-checked') !== null"
            " || (el.querySelector && el.querySelector('input')?.checked === true)"
            " || el.checked === true"))
    except Exception:  # noqa: BLE001 - 元素没了
        return True        # 判断不了就当已勾，避免反复点


def _is_switch(element) -> bool:
    """这个勾是开关（帮助改进模型），不是同意条款。开关一律不碰。"""
    try:
        return bool(element.evaluate(
            "(el) => !!(el.closest('[role=switch]')"
            " || el.parentElement?.querySelector('[role=switch]'))"))
    except Exception:  # noqa: BLE001 - 判断不了就当开关，不点
        return True


def _consent_boxes(page) -> list:
    return [e for e in _all_visible(page, CHECKBOX_SEL) if not _is_switch(e)]


def _value(element) -> str | None:
    """输入框里现在的内容；读不到返回 None。"""
    try:
        return element.input_value()
    except Exception:  # noqa: BLE001 - 元素没了
        return None


def _all_visible(page, selector: str, limit: int = 12) -> list:
    try:
        locator = page.locator(selector)
        total = locator.count()
    except Exception:  # noqa: BLE001 - 页面跳转中
        return []
    return [locator.nth(i) for i in range(min(total, limit)) if _visible(locator.nth(i))]


# ---------------------------------------------------------------- 页面判断


def chat_ready(page) -> bool:
    try:
        return page.locator(APP_INPUT).count() > 0
    except Exception:  # noqa: BLE001 - 跳转中
        return False


def _url(page) -> str:
    try:
        return page.url or ""
    except Exception:  # noqa: BLE001 - 拿不到地址就当没有
        return ""


def page_signature(page) -> str:
    """页面签名：地址 + 主标题。只用来判断"页面变没变"，不解读文字。"""
    try:
        return page.evaluate(
            "() => location.href + '|' + "
            "(document.querySelector('h1, h2, [role=heading]')?.innerText || '').trim()")
    except Exception:  # noqa: BLE001 - 跳转中
        return ""


def _cards(page) -> list:
    """整块选项卡片：class 有 w-full，且没有 group/btn（那是 CTA 按钮的样式）。"""
    cards = []
    for element in _all_visible(page, CARD_SEL):
        cls = _attr(element, "class")
        if "w-full" in cls and "group/btn" not in cls and _safe(element):
            cards.append(element)
    return cards


def _continue_button(page):
    """主按钮（继续）：class 带 group/btn，不是 ghost，不是购买。"""
    for element in _all_visible(page, BUTTON_SEL):
        cls = _attr(element, "class")
        if "group/btn" not in cls or not _safe(element):
            continue
        if _attr(element, "data-cds-ghost") or _attr(element, "data-cta"):
            continue
        return element
    return None


def _submit_button(page):
    for element in _all_visible(page, 'button[type="submit"]'):
        if _safe(element):
            return element
    return None


def _combo(page):
    return _first_visible(page.locator(COMBO_SEL), limit=4)


def _combo_open(page) -> bool:
    """下拉列表是不是正展开着。展开时卡片被盖住，必须先处理列表。"""
    combo = _combo(page)
    return combo is not None and _attr(combo, "aria-expanded") == "true"


def classify(page) -> str:  # noqa: PLR0911 - 一个类型一个分支，拆开反而看不清优先级
    """这是哪一类页面。顺序就是优先级。"""
    if UPGRADE_URL_RE.search(_url(page)):
        return "upgrade"
    if _consent_boxes(page):
        return "consent"
    for element in _all_visible(page, TEXT_INPUT_SEL, limit=4):
        value = _value(element)
        if value is not None and not value.strip():
            return "form"
    if _combo_open(page):
        return "pick"
    if _cards(page):
        return "choice"
    if _combo(page) is not None:
        return "select"
    if _all_visible(page, GHOST_SEL, limit=1):
        return "skip"
    if _continue_button(page) is not None:
        return "continue"
    return "unknown"


# ---------------------------------------------------------------- 各类页面的动作


def _act_consent(page) -> bool:
    """勾一个没勾的（从下往上），勾不到就提交。"""
    for element in reversed(_consent_boxes(page)):
        if not _checked(element) and _click(element):
            log_line("勾选同意项")
            return True
    button = _submit_button(page) or _continue_button(page)
    if button is not None and _click(button):
        log_line("提交")
        return True
    return False


def _act_form(page, name: str) -> bool:
    for element in _all_visible(page, TEXT_INPUT_SEL, limit=4):
        value = _value(element)
        if value is None or value.strip():
            continue
        if _click(element):
            page.keyboard.type(name)
            log_line("填写名字")
            return True
        return False
    button = _submit_button(page) or _continue_button(page)
    if button is not None and _click(button):
        log_line("提交")
        return True
    return False


def _escape(page) -> None:
    with suppress(Exception):
        page.keyboard.press("Escape")


def _act_select(page) -> bool:
    """打开下拉列表。"""
    combo = _combo(page)
    if combo is not None and _click(combo):
        log_line("打开下拉框")
        return True
    return False


def _act_pick(page) -> bool:
    """列表展开时：选一个，然后把列表合上。

    已经选过就只合上，不再改选。有些版本选完不会自动合，
    所以点完统一补一下 Esc。
    """
    options = _all_visible(page, OPTION_SEL, limit=8)
    if not options:
        _escape(page)
        log_line("列表里没有选项，按 Esc 合上")
        return True
    if any(_attr(element, "aria-selected") == "true" for element in options):
        _escape(page)
        log_line("已经选过，合上列表")
        return True
    if not _click(options[0]):
        return False
    log_line("选了一个选项")
    page.wait_for_timeout(400)
    _escape(page)
    return True


def _act_choice(page) -> bool:
    cards = _cards(page)
    if cards and _click(cards[0]):
        log_line("点第一个选项卡片")
        return True
    return False


def _act_skip(page) -> bool:
    ghost = _first_visible(page.locator(GHOST_SEL), limit=4)
    if ghost is not None and _click(ghost):
        log_line("点次要按钮（跳过）")
        return True
    return False


def _act_continue(page) -> bool:
    button = _continue_button(page)
    if button is not None and _click(button):
        log_line("点继续")
        return True
    return False


def _act_upgrade(page) -> bool:
    """升级页：只点不带购买标记的按钮（关闭、免费继续都算）。

    买套餐的按钮一律不点。找不到能点的就什么都不做，
    交给主循环按 Esc、回聊天页。
    """
    for selector in ("button:not([data-cta])",
                     'a[role="button"]:not([data-cta])'):
        element = _first_visible(page.locator(selector), limit=6)
        if element is not None and _click(element):
            log_line("升级页：点免费继续")
            return True
    return False


ACTIONS = {
    "consent": _act_consent,
    "select": _act_select,
    "pick": _act_pick,
    "choice": _act_choice,
    "skip": _act_skip,
    "continue": _act_continue,
    "upgrade": _act_upgrade,
}


def _act(page, name: str, kind: str) -> bool:
    """按类型做这一页该做的事。只有表单页需要名字。"""
    if kind == "form":
        return _act_form(page, name)
    return ACTIONS[kind](page)


# ---------------------------------------------------------------- 主循环


def _settle(page, before: str) -> str:
    """动作之后等页面跳一下再取签名；跳不动就返回当前签名。"""
    deadline = time.time() + SETTLE_S
    while time.time() < deadline:
        current = page_signature(page)
        if current != before:
            return current
        page.wait_for_timeout(250)
    return page_signature(page)


def _stall_action(page, stalled: int) -> str:
    """一直没进展时的补救。返回要结束的状态，空串表示继续。"""
    if stalled >= STUCK_LIMIT:
        log_line("onboarding 卡住：页面一直没变")
        return "stuck"
    if stalled == ESC_AFTER_STALL:
        log_line("没东西可点，按 Esc 关弹层")
        with suppress(Exception):
            page.keyboard.press("Escape")
    elif stalled == JUMP_AFTER_STALL:
        log_line("直接跳到聊天页")
        with suppress(Exception):     # 跳不过去就继续等
            page.goto("https://claude.ai/new", timeout=60000)
    return ""


def _do(page, name: str, kind: str, signature: str, state: dict) -> bool:
    """做这一类页面的动作。同一页的下拉只选一次。"""
    if kind != "pick":
        return _act(page, name, kind)
    if signature in state["picked"]:
        _escape(page)               # 这页选过了，只把还开着的列表合上
        log_line("这页选过了，合上列表")
        return True
    acted = _act(page, name, kind)
    if acted:
        state["picked"].add(signature)
    return acted


def _unknown(page, count: int) -> str:
    """不认识的页面：记一次，够数就停。返回要结束的状态，空串表示继续。"""
    log_line(f"不认识的页面（第 {count} 次）")
    if count >= UNKNOWN_LIMIT:
        log_line("不认识的页面太多，停下")
        return "stuck"
    page.wait_for_timeout(1000)
    return ""


def complete(page, name: str, *, timeout: float, tick=None) -> str:
    """把 onboarding 走完。返回 ok / closed / timeout / stuck。"""
    deadline = time.time() + timeout
    signature = page_signature(page)
    stalled = 0
    unknown = 0
    state = {"picked": set()}
    while time.time() < deadline:
        if window_closed(page):
            return "closed"
        if tick is not None:
            tick(page)
        if chat_ready(page):
            return "ok"

        kind = classify(page)
        if kind not in ACTIONS and kind != "form":
            unknown += 1
            result = _unknown(page, unknown)
            if result:
                return result
            continue

        before = signature
        acted = _do(page, name, kind, signature, state)
        signature = _settle(page, before) if acted else page_signature(page)
        if signature != before:
            stalled = 0
            unknown = 0
            low, high = PAGE_PAUSE_MIN_MS, PAGE_PAUSE_MAX_MS
        else:
            stalled += 1
            low, high = STEP_PAUSE_MIN_MS, STEP_PAUSE_MAX_MS

        if acted:
            page.wait_for_timeout(random.randint(low, high))  # noqa: S311 - 节奏抖动
            continue

        result = _stall_action(page, stalled)
        if result:
            return result
        page.wait_for_timeout(500)
    return "timeout"
