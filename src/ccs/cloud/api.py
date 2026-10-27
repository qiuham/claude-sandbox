"""云端接口：只负责收发，不打印。错误统一抛 CloudError。"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request

TIMEOUT_S = 60
UA = "ccs-cloud/1"
HTTP_NOT_FOUND = 404


class CloudError(Exception):
    """服务端回了 4xx / 5xx。payload 里是 {"error": "..."} 或冲突详情。"""

    def __init__(self, status: int, payload: dict):
        self.status = status
        self.payload = payload
        super().__init__(str(payload.get("error") or f"HTTP {status}"))


def _where(base: str, path: str, query: dict | None) -> str:
    url = base.rstrip("/") + path
    if query:
        url += "?" + urllib.parse.urlencode(query)
    return url


def _call(method: str, base: str, path: str, token: str, *,  # noqa: PLR0913 - 请求就这几样
          query: dict | None = None, body: bytes | None = None,
          content_type: str = "application/gzip") -> tuple[bytes, dict]:
    req = urllib.request.Request(  # noqa: S310 - 地址是本机配置
        _where(base, path, query), data=body, method=method)
    req.add_header("User-Agent", UA)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if body is not None:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as resp:  # noqa: S310 - 地址是本机配置
            return resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            payload = {"error": raw.decode("utf-8", "replace")[:200]}
        raise CloudError(exc.code, payload) from exc
    except urllib.error.URLError as exc:
        raise CloudError(0, {"error": f"连不上云端 {base}：{exc.reason}"}) from exc


def _json(base: str, path: str, token: str, method: str = "GET",  # noqa: PLR0913, PLR0917
          query: dict | None = None, body: bytes | None = None) -> dict:
    raw, _ = _call(method, base, path, token, query=query, body=body)
    return json.loads(raw or b"{}")


def email_path(email: str) -> str:
    return urllib.parse.quote(email.lower(), safe="@")


def whoami(base: str, token: str) -> dict:
    return _json(base, "/v1/me", token)


def accounts(base: str, token: str) -> list[dict]:
    return list(_json(base, "/v1/accounts", token).get("accounts") or [])


def versions(base: str, token: str, email: str) -> list[dict]:
    path = f"/v1/accounts/{email_path(email)}/versions"
    return list(_json(base, path, token).get("versions") or [])


def find(base: str, token: str, email: str, sha: str) -> int | None:
    """这份内容在云端历史里是哪一版。找不到返回 None。"""
    path = f"/v1/accounts/{email_path(email)}/find"
    try:
        return int(_json(base, path, token, query={"sha256": sha}).get("version") or 0)
    except CloudError as exc:
        if exc.status == HTTP_NOT_FOUND:
            return None
        raise


def download(base: str, token: str, email: str,
             version: int | None = None) -> tuple[bytes, dict]:
    query = {"version": version} if version else None
    body, headers = _call("GET", base, f"/v1/accounts/{email_path(email)}", token,
                          query=query)
    return body, {
        "version": int(headers.get("X-Version") or 0),
        "sha256": str(headers.get("X-Sha256") or ""),
        "sandbox": urllib.parse.unquote(str(headers.get("X-Sandbox") or "")),
    }


def upload(base: str, token: str, email: str, *, sha: str,  # noqa: PLR0913 - 上传要带全
           base_version: int, message: str, force: bool, body: bytes) -> dict:
    query = {"base": base_version, "message": message, "force": int(force), "sha256": sha}
    return _json(base, f"/v1/accounts/{email_path(email)}/versions", token,
                 method="POST", query=query, body=body)


def create_user(base: str, token: str, name: str) -> dict:
    body = json.dumps({"name": name}).encode()
    raw, _ = _call("POST", base, "/v1/users", token, body=body,
                   content_type="application/json")
    return json.loads(raw or b"{}")


def delete(base: str, token: str, email: str) -> dict:
    return _json(base, f"/v1/accounts/{email_path(email)}", token, method="DELETE")
