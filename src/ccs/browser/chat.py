"""日常对话：找输入框、发一句话、等回复。"""

from __future__ import annotations

import time

from ccs import ui
from ccs.browser.account import harvest_account
from ccs.browser.prompts import load_prompts, pick_prompt, remember_prompt
from ccs.config import config_timeouts
from ccs.errors import log_line, say

WARMUP_TEXT = {
    "ok": "已收到回复",
    "no_input": "找不到输入框（可能没登录）",
    "not_sent": "消息没发出去（可能没登录）",
    "no_reply": "发了但没等到回复（看 ccs.log）",
}


def find_input(page, cfg: dict) -> str:
    for candidate in ('div[contenteditable="true"]', '[data-testid="chat-input"]', "textarea"):
        try:
            page.wait_for_selector(candidate, timeout=config_timeouts(cfg)["selector_ms"])
        except Exception:  # noqa: BLE001, S112 - 找不到这个选择器就试下一个，不记日志
            continue
        else:
            return candidate
    return ""


def composer_text(page, selector: str) -> str | None:
    """读输入框里的文字；读不到返回 None。零宽字符也算空。"""
    try:
        text = page.eval_on_selector(
            selector, "el => el.innerText || el.textContent || ''")
    except Exception:  # noqa: BLE001 - 页面关掉或选择器失效都当读不到
        return None
    return str(text).replace("\u200b", "").replace("\ufeff", "").strip()


def wait_for_sent(page, selector: str, cfg: dict) -> bool:
    """输入框变空 = 消息真的发出去了；一直有字 = Enter 没生效。"""
    warmup = cfg["warmup"]
    deadline = time.time() + warmup["send_confirm_s"]
    while time.time() < deadline:
        time.sleep(warmup["poll_interval_s"])
        text = composer_text(page, selector)
        if text == "":
            return True
        if text is None:
            return False
    return False


def wait_for_growth(page, base: int, cfg: dict) -> bool:
    """等页面文本继续增长（用来确认模型回复了）。"""
    warmup = cfg["warmup"]
    deadline = time.time() + warmup["reply_wait_s"]
    while time.time() < deadline:
        time.sleep(warmup["poll_interval_s"])
        try:
            if len(page.inner_text("body")) > base + warmup["min_growth"]:
                return True
        except Exception:  # noqa: BLE001 - 页面被关掉就当没等到
            return False
    return False


def do_warmup(page, prompt: str, cfg: dict) -> str:
    """发一句话并等回复。返回 ok / no_input / not_sent / no_reply 四种结果。"""
    warmup = cfg["warmup"]
    selector = find_input(page, cfg)
    if not selector:
        return "no_input"

    try:
        page.locator(selector).first.click()   # 引擎负责接近路径和偏心落点
        page.keyboard.type(prompt)             # 引擎的 TypingPersona 负责按键节奏
        page.keyboard.press("Enter")
    except Exception as exc:  # noqa: BLE001 - 点击/输入失败只算这次对话失败
        log_line(f"对话失败：{type(exc).__name__}: {exc}")
        return "not_sent"

    # 第一关：输入框变空才算发出去。字还在输入框 = Enter 没生效。
    if not wait_for_sent(page, selector, cfg):
        log_line("对话没发出：输入框没有变空")
        return "not_sent"

    # 基准在输入框空了之后取，避免把还没清掉的提示文字算进去
    try:
        before = len(page.inner_text("body"))
    except Exception:  # noqa: BLE001 - 读不到就按 0 算
        before = 0
    if wait_for_growth(page, before, cfg):
        return "ok"
    try:
        after = len(page.inner_text("body"))
    except Exception:  # noqa: BLE001 - 读不到就按 0 算
        after = 0
    log_line(f"对话没等到回复：页面 {after} 字符，基准 {before}，"
             f"等了 {warmup['reply_wait_s']} 秒")
    return "no_reply"


def warmup_flow(page, name: str, prompt: str | None, cfg: dict) -> int:
    prompts = load_prompts(cfg)
    prompt = (prompt or pick_prompt(name, prompts, cfg)).strip()
    say(ui.kv("对话", f"发送「{prompt}」"))
    result = do_warmup(page, prompt, cfg)
    say(ui.kv("对话", WARMUP_TEXT.get(result, result)))
    log_line(f"对话结果: {result} · {prompt}")
    harvest_account(page, name)
    if result == "ok":
        remember_prompt(name, prompt)
    return 0
