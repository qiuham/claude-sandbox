"""录制：把人工操作完整记下来，用来学流程。

用法：`ccs <命令> --record`，人正常操作，关窗口后看
`~/sandboxes/<名字>/record/`：

  actions.jsonl   每次点击/输入/勾选/跳转：元素、祖先按钮、页面标题、可见控件、网址、截图
  shot-0001.png   事件发生时的窗口截图

只记录，不影响页面行为。密码框的值记成 `***`。

字段说明（每条事件）：

  kind      click / input / change / nav
  el        事件真正打在哪个元素上
  control   最近的可操作祖先（按钮、链接、输入框、选项…）——图标按钮靠它认
  label     输入框关联的标签文字
  heading   当时的主标题，用来判断是哪一步
  controls  当时页面上所有可见的按钮/链接文字
  url       事件发生时的地址
  shot      对应截图
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from ccs.errors import log_line

if TYPE_CHECKING:
    from pathlib import Path

# 在每次导航后注入：把事件攒在 window.__ccsRec，Python 定时取走。
RECORD_JS = """
(() => {
  if (window.__ccsRec) return;
  window.__ccsRec = [];

  const path = (el) => {
    const parts = [];
    for (let n = el; n && n.nodeType === 1 && parts.length < 6; n = n.parentElement) {
      let s = n.tagName.toLowerCase();
      if (n.id && n.id.indexOf('base-ui-') !== 0) { s += '#' + n.id; parts.unshift(s); break; }
      const cls = (n.className || '').toString().trim().split(/\\s+/).filter(Boolean)[0];
      if (cls) s += '.' + cls;
      const idx = n.parentElement
        ? Array.from(n.parentElement.children).indexOf(n) + 1 : 0;
      if (idx) s += `:nth-child(${idx})`;
      parts.unshift(s);
    }
    return parts.join(' > ');
  };

  const desc = (el) => {
    if (!el || !el.getAttribute) return null;
    const attrs = {};
    for (const a of (el.attributes || [])) {
      if (a.name === 'value') continue;
      attrs[a.name] = String(a.value).slice(0, 80);
    }
    return {
      tag: (el.tagName || '').toLowerCase(),
      role: el.getAttribute('role') || '',
      type: el.getAttribute('type') || '',
      id: el.id || '',
      name: el.getAttribute('name') || '',
      aria: el.getAttribute('aria-label') || el.getAttribute('title') || '',
      testid: el.getAttribute('data-testid') || '',
      placeholder: el.getAttribute('placeholder') || '',
      text: (el.innerText || el.textContent || '').trim().slice(0, 120),
      checked: el.checked === true,
      disabled: el.disabled === true || el.getAttribute('aria-disabled') === 'true',
      path: path(el),
      attrs,
    };
  };

  const ACTIONABLE = 'button, a, input, select, textarea, label, '
    + '[role="button"], [role="link"], [role="checkbox"], [role="switch"], '
    + '[role="combobox"], [role="option"], [role="radio"], [role="menuitem"], [role="tab"]';

  const controlOf = (el) => {
    let n = el;
    for (let i = 0; n && n.getAttribute && i < 6; i++, n = n.parentElement) {
      if (n.matches && n.matches(ACTIONABLE)) return n;
    }
    return el;
  };

  const labelOf = (el) => {
    if (!el || !el.getAttribute) return '';
    if (el.id) {
      const byFor = document.querySelector(`label[for="${el.id}"]`);
      if (byFor) return (byFor.innerText || '').trim().slice(0, 120);
    }
    const near = el.closest('label');
    if (near) return (near.innerText || '').trim().slice(0, 120);
    const ids = (el.getAttribute('aria-labelledby') || '').split(/\\s+/).filter(Boolean);
    return ids.map((id) => (document.getElementById(id)?.innerText || ''))
      .join(' ').trim().slice(0, 120);
  };

  const headingOf = () => {
    const h = document.querySelector('h1, h2, [role="heading"]');
    return h ? (h.innerText || '').trim().slice(0, 120) : '';
  };

  const visibleControls = () => {
    const out = [];
    const seen = new Set();
    for (const el of document.querySelectorAll(ACTIONABLE)) {
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      const text = ((el.innerText || el.getAttribute('aria-label')
        || el.getAttribute('placeholder') || el.getAttribute('title') || '') + '').trim();
      if (!text || text.length > 60 || seen.has(text)) continue;
      seen.add(text);
      out.push(text);
      if (out.length >= 30) break;
    }
    return out;
  };

  const push = (kind, target, extra) => {
    const el = (target && target.nodeType === 1) ? target : document.activeElement;
    const control = controlOf(el);
    window.__ccsRec.push(Object.assign({
      ts: Date.now(),
      kind,
      el: desc(el),
      control: desc(control) || desc(el),
      label: labelOf(el),
      heading: headingOf(),
      controls: visibleControls(),
    }, extra || {}));
  };

  document.addEventListener('click', (e) => push('click', e.target), true);
  document.addEventListener('input', (e) => {
    const t = e.target;
    const masked = (t && t.type === 'password') ? '***'
      : String((t && t.value) || '').slice(0, 120);
    push('input', t, { value: masked });
  }, true);
  document.addEventListener('change', (e) => push('change', e.target), true);
})();
"""

DRAIN_JS = """
() => { const e = window.__ccsRec || []; window.__ccsRec = []; return e; }
"""

PAGE_INFO_JS = """
() => ({
  url: location.href,
  title: document.title,
  heading: (document.querySelector('h1, h2, [role="heading"]')?.innerText || '')
    .trim().slice(0, 120),
})
"""

MAX_SHOTS = 400
# 小于这个字节数的截图多半是过渡期的纯色图
BLANK_SHOT_BYTES = 5000


class Recorder:
    """把人工操作记成 `actions.jsonl` + 截图。"""

    def __init__(self, out_dir: Path):
        self.dir = out_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.log = self.dir / "actions.jsonl"
        self.shots = 0
        self.events = 0
        self.last_url = ""

    def start(self, page) -> None:
        context = getattr(page, "context", None)
        if context is not None:
            # 装在 context 上：新开标签页也自动带脚本
            context.add_init_script(RECORD_JS)
        page.add_init_script(RECORD_JS)
        try:
            page.evaluate(RECORD_JS)          # 当前页也装上（init script 只对下次导航生效）
        except Exception as exc:  # noqa: BLE001 - 当前页装不上就只记后续导航
            log_line(f"录制脚本注入失败: {type(exc).__name__}")
        log_line(f"开始录制: {self.dir}")

    def _write(self, event: dict, page) -> None:
        if self.shots >= MAX_SHOTS:
            return
        self.events += 1
        self.shots += 1
        name = f"shot-{self.shots:04d}.png"
        try:
            shot = self.dir / name
            page.screenshot(path=str(shot), full_page=False)
            # 过渡期可能拍到纯色图（文件很小），等一下重拍一次
            if shot.stat().st_size < BLANK_SHOT_BYTES:
                page.wait_for_timeout(400)
                page.screenshot(path=str(shot), full_page=False)
        except Exception:  # noqa: BLE001 - 截图失败不影响记录
            name = ""
        event["shot"] = name
        event["wall"] = time.strftime("%Y-%m-%d %H:%M:%S")
        event.pop("ts", None)
        with self.log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")

    def _page_info(self, page) -> dict:
        try:
            return page.evaluate(PAGE_INFO_JS) or {}
        except Exception:  # noqa: BLE001 - 跳转中拿不到
            return {}

    def poll(self, page) -> None:
        """取走新事件；顺便把页面跳转也记下来。"""
        info = self._page_info(page)
        url = str(info.get("url") or "")
        if url and url != self.last_url:
            self.last_url = url
            self._write({"kind": "nav", "url": url,
                         "title": info.get("title") or "",
                         "heading": info.get("heading") or ""}, page)
        try:
            events = page.evaluate(DRAIN_JS) or []
        except Exception:  # noqa: BLE001 - 页面关了或跳转中
            return
        for event in events:
            event["url"] = url or str(getattr(page, "url", ""))
            event.setdefault("title", info.get("title") or "")
            self._write(event, page)
