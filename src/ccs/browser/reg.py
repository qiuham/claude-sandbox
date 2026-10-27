"""注册 / 重新登录的命令层：选邮箱 → 建或复用地址 → 交给 signin 走页面。

页面怎么填、邮件怎么等，全在 `signin.py`；这里只决定"用哪个邮箱"和"要不要建"：

- `ccs reg`：新号用哈希地址，老号复用已登记的邮箱；地址已存在就复用。
- `ccs login`：只用已登记的邮箱，绝不新建地址。

继续不做：不重试、不判失败、不自动开始对话。
"""

from __future__ import annotations

import hashlib

from ccs import paths, ui
from ccs.browser.mail import MailClient, MailError
from ccs.browser.signin import sign_in
from ccs.errors import die, say
from ccs.sandbox import store

STATUS_TEXT = {
    "ok": "登录成功，账号信息已记录",
    "no_input": "没等到邮箱表单（可能卡在人机验证，或页面结构变了）",
    "bad_email": "邮箱没填完整，没有提交（页面可能在打字过程中刷新了）",
    "no_mail": "邮件服务没返回新邮件",
    "closed": "窗口已关闭，流程中止",
    "code_only": "邮件给的是验证码，请在窗口里输入，然后看窗口是否进入主界面",
    "no_session": "链接打开了但没拿到登录态（可能卡在验证码）",
    "onboarding": "已登录，但 onboarding 没走完（页面变了或卡住，窗口已关闭）",
}


def local_part(name: str) -> str:
    """默认地址名：沙盒名 + 指纹种子的哈希，取 10 位。

    不用 `claude…` 这类固定前缀：上一批号全是同一个模式，域名也跟着被标。
    """
    seed, _created = store.seed_of(paths.profile_dir(name))
    return hashlib.sha256(f"{name}:{seed}".encode()).hexdigest()[:10]


def mail_settings(cfg: dict) -> tuple[str, str, str]:
    """读 `[mail]`：base / admin_auth / domain。缺任何一个都不继续。"""
    mail = cfg.get("mail") or {}
    base = str(mail.get("base") or "").strip()
    auth = str(mail.get("admin_auth") or "").strip()
    domain = str(mail.get("domain") or "").strip()
    if not (base and auth and domain):
        die("没配收信服务：在 ccs.toml 里填 [mail] 的 base / admin_auth / domain")
    return base, auth, domain


def _pick_email(name: str, given: str, existing: str, domain: str) -> str:
    """这次用哪个邮箱：手动指定 > 号里登记过的 > 哈希新地址。"""
    if given:
        return given if "@" in given else f"{given}@{domain}"
    if existing:
        return existing
    return f"{local_part(name)}@{domain}"


def register(page, name: str, cfg: dict, email: str = "", *,  # noqa: PLR0913 - 参数就是这几样
             login_only: bool = False, tick=None) -> int:
    """注册或重新登录。成功返回 0；卡住返回 1 并说清楚卡在哪一步。"""
    base, auth, domain = mail_settings(cfg)
    account = store.read_account(name)
    existing = str(account.get("email") or "").strip()
    if login_only and not (email or existing):
        die(f"{name} 没登记过邮箱，没法重新登录；注册请用 ccs reg {name}")

    address = _pick_email(name, email, existing, domain)
    local, _, mail_domain = address.partition("@")
    client = MailClient(base, auth)

    try:
        if login_only:
            jwt = client.recover_jwt(address)
            say(ui.kv("邮箱", f"{address}（复用已有）"))
        else:
            created = client.ensure_address(local, mail_domain or domain)
            address = str(created.get("address") or address)
            reused = bool(existing) and existing.lower() == address.lower()
            say(ui.kv("邮箱", f"{address}（{'复用已有' if reused else '新建'}）"))
            jwt = str(created.get("jwt") or "")
    except MailError as exc:
        say(ui.kv("收信失败", str(exc)))
        return 1

    status = sign_in(page, name=name, email=address, client=client, jwt=jwt, cfg=cfg, tick=tick)
    say(ui.kv("结果", STATUS_TEXT[status]))
    return 0 if status in ("ok", "code_only", "onboarding") else 1
