"""临时邮箱：建地址、收信、从邮件里抠登录链接或验证码。

接口是自建的 Cloudflare Worker（cloudflare_temp_email）：

  POST /admin/new_address     头 x-admin-auth  {"name","domain"}
    → {"address","jwt","address_id"}
  GET  /api/parsed_mails?limit=&offset=     头 Authorization: Bearer <jwt>

三条实测踩过的坑：
1. 建地址只传 local part，带 @ 或 . 会被服务端截断。
2. 这个服务跟 Claude 无关：走本机环境里的代理，不走沙盒的出口。
3. UA 必须是浏览器，默认的 Python-urllib 会被 Cloudflare 拦（错误 1010）。
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import NoReturn

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

# 魔术链接；token 在 # 后面（fragment），必须在同一个浏览器上下文里打开。
MAGIC_LINK_RE = re.compile(r"https://claude\.ai/[^\s\"'>]*magic[^\s\"'>]*")
# 备选路径：6 位验证码。带关键词的优先，避免命中邮件里的其它数字。
CODE_KEYWORD_RE = re.compile(r"(?:code|验证码|verification)[^\d]{0,24}(\d{6})",
                             re.IGNORECASE)
CODE_BARE_RE = re.compile(r"\b(\d{6})\b")

LOGIN_LINK_SUBJECT = "secure link"


class MailError(Exception):
    """收信服务返回了不能继续的错误。"""


HTTP_ERROR = 400


def _fail(message: str) -> NoReturn:
    raise MailError(message)


def _received_after(mail: dict, since_ts: float, *, grace_s: float = 60) -> bool:
    """这封邮件是不是提交之后到的。

    `created_at` 是服务器时钟（UTC）；留 60 秒余量，防两台机器的时钟有几秒偏差。
    解析不了时间的邮件不拦（宁可多看一眼，也不漏掉）。
    """
    if not since_ts:
        return True
    raw = str(mail.get("created_at") or "").strip()
    if not raw:
        return True
    try:
        stamp = datetime.strptime(raw, "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        try:
            stamp = datetime.fromisoformat(raw)
        except ValueError:
            return True
    return stamp.timestamp() >= since_ts - grace_s


def magic_link(mail: dict) -> str:
    """邮件正文里的魔术链接；没有返回空串。"""
    body = " ".join(str(mail.get(key) or "")
                    for key in ("text", "body", "html", "raw", "content"))
    body = body.replace("&amp;", "&")
    return MAGIC_LINK_RE.search(body).group(0) if MAGIC_LINK_RE.search(body) else ""


def verification_code(mail: dict) -> str:
    """邮件正文里的 6 位验证码；没有返回空串。"""
    body = " ".join(str(mail.get(key) or "")
                    for key in ("subject", "text", "body", "html", "raw", "content"))
    hit = CODE_KEYWORD_RE.search(body) or CODE_BARE_RE.search(body)
    return hit.group(1) if hit else ""


class MailClient:
    """管理员建地址，用地址自己的 JWT 读信。"""

    def __init__(self, base: str, admin_auth: str, *, timeout_s: float = 20):
        self.base = base.rstrip("/")
        self.admin_auth = admin_auth
        self.timeout_s = timeout_s
        # 用环境里的代理（本机 Clash 或直连），不走沙盒出口。

    def _call(self, method: str, path: str, body: dict | None = None,
              headers: dict | None = None) -> tuple[int, str]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(  # noqa: S310 - 地址来自配置
            self.base + path, data=data, method=method,
            headers={"User-Agent": BROWSER_UA, "Accept": "application/json",
                     "Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(  # noqa: S310 - 地址来自配置
                    request, timeout=self.timeout_s) as response:
                return response.status, response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            return exc.code, exc.read().decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError) as exc:
            _fail(f"连不上收信服务: {type(exc).__name__}")

    def new_address(self, name: str, domain: str) -> dict:
        """建一个地址。返回 {address, jwt, address_id}。"""
        if "@" in name or "." in name:
            _fail("地址名只能传 local part，不能带 @ 或 .")
        code, raw = self._call("POST", "/admin/new_address",
                               {"name": name, "domain": domain},
                               {"x-admin-auth": self.admin_auth})
        if code >= HTTP_ERROR:
            _fail(f"建地址失败 HTTP {code}: {raw[:200]}")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            _fail(f"建地址响应不是 JSON: {raw[:200]}")
        data = data.get("data") if isinstance(data.get("data"), dict) else data
        if not data.get("address") or not data.get("jwt"):
            _fail(f"建地址响应缺 address/jwt: {raw[:200]}")
        return data

    def addresses(self, *, limit: int = 100) -> list[dict]:
        """管理员看所有地址。"""
        code, raw = self._call("GET", f"/admin/address?limit={limit}&offset=0",
                               headers={"x-admin-auth": self.admin_auth})
        if code >= HTTP_ERROR:
            _fail(f"列出地址失败 HTTP {code}: {raw[:200]}")
        data = json.loads(raw) if raw else {}
        results = data.get("results") if isinstance(data, dict) else None
        return results if isinstance(results, list) else []

    def find_address_id(self, email: str) -> int:
        """按完整地址找 id；找不到返回 0。"""
        for item in self.addresses():
            if str(item.get("name") or "").lower() == email.lower():
                return int(item.get("id") or 0)
        return 0

    def recover_jwt(self, email: str) -> str:
        """地址已存在时，让管理员重签一个 JWT（show_password 返回的是 JWT，不是登录密码）。"""
        address_id = self.find_address_id(email)
        if not address_id:
            _fail(f"地址 {email} 不在收信服务里，也没法重建")
        code, raw = self._call("GET", f"/admin/show_password/{address_id}",
                               headers={"x-admin-auth": self.admin_auth})
        if code >= HTTP_ERROR:
            _fail(f"取回地址凭据失败 HTTP {code}: {raw[:200]}")
        try:
            jwt = str(json.loads(raw).get("jwt") or "")
        except json.JSONDecodeError:
            jwt = ""
        if not jwt:
            _fail(f"取回地址凭据的响应里没有 jwt: {raw[:200]}")
        return jwt

    def ensure_address(self, name: str, domain: str) -> dict:
        """建地址；已存在就复用（取回 JWT）。返回 {address, jwt, address_id}。"""
        email = f"{name}@{domain}"
        try:
            return self.new_address(name, domain)
        except MailError as exc:
            if "已存在" not in str(exc):
                raise
            return {"address": email, "jwt": self.recover_jwt(email), "address_id": 0}

    def delete_address(self, address_id: int) -> None:
        self._call("DELETE", f"/admin/delete_address/{int(address_id)}",
                   headers={"x-admin-auth": self.admin_auth})

    def mails(self, jwt: str, *, limit: int = 20) -> list[dict]:
        """最近的信，新的在前。"""
        code, raw = self._call("GET", f"/api/parsed_mails?limit={limit}&offset=0",
                               headers={"Authorization": f"Bearer {jwt}"})
        if code >= HTTP_ERROR:
            _fail(f"读信失败 HTTP {code}: {raw[:200]}")
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            _fail(f"读信响应不是 JSON: {raw[:200]}")
        results = data.get("results") if isinstance(data, dict) else None
        return results if isinstance(results, list) else []

    def wait_for_login_link(self, jwt: str, *, timeout_s: float = 300,
                            interval_s: float = 3, since_ts: float = 0) -> tuple[str, str]:
        """等 Claude 的登录邮件。返回 `(链接, 验证码)`，两个都可能为空串。

        只认主题带 "secure link" 的邮件，避免把注册欢迎信里的链接当成登录链接。
        """
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            # 旧邮件不要：复用的邮箱里还留着上一次的登录链接，打开也登不上。
            mails = [m for m in self.mails(jwt)
                     if _received_after(m, since_ts)]
            # 优先：主题写着 secure link 的那封
            for mail in mails:
                if LOGIN_LINK_SUBJECT in str(mail.get("subject") or "").lower():
                    link = magic_link(mail)
                    if link:
                        return link, ""
            # 其次：任何一封带魔术链接的（主题可能会改）
            for mail in mails:
                link = magic_link(mail)
                if link:
                    return link, ""
            for mail in mails:
                code = verification_code(mail)
                if code:
                    return "", code
            time.sleep(interval_s)
        return "", ""
