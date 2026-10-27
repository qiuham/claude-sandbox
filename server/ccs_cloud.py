#!/usr/bin/env python3
"""ccs-cloud：账号包同步服务。单文件，只用标准库。

**为什么按邮箱存**：本地名字（006）两个人可能都叫同一个，邮箱不会重复。

**为什么上传不覆盖**：每次上传都是一个新版本，要声明基于哪一版。
不是最新就拒绝，客户端先拉再推。旧版本都留着，能用 revert 退回去。

数据目录（默认 /data）：
  cloud.db           用户、账号、版本索引
  blobs/<sha>.tar.gz 包内容，按内容哈希去重

环境变量：
  CCS_CLOUD_DATA         数据目录，默认 /data
  CCS_CLOUD_PORT         监听端口，默认 8787
  CCS_CLOUD_ADMIN_TOKEN  首次启动时用它建管理员；不给就随机生成并打印
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import secrets
import sqlite3
import sys
import tarfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlparse

DATA = Path(os.environ.get("CCS_CLOUD_DATA") or "/data")
MAX_BODY = 32 * 1024 * 1024          # 一个包最大 32 MB，防止误传大文件
KIND = "ccs-sandbox"
PATH_ACCOUNT = 3                     # /v1/accounts/<邮箱>
PATH_ACTION = 4                      # /v1/accounts/<邮箱>/<动作>
_LOCK = threading.Lock()


# ---------------------------------------------------------------- 存储


def db() -> sqlite3.Connection:
    con = sqlite3.connect(DATA / "cloud.db", timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    return con


def init() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "blobs").mkdir(exist_ok=True)
    with db() as con:
        con.executescript("""
        CREATE TABLE IF NOT EXISTS users (
          id INTEGER PRIMARY KEY,
          name TEXT UNIQUE NOT NULL,
          token_hash TEXT NOT NULL,
          role TEXT NOT NULL DEFAULT 'member',
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS accounts (
          email TEXT PRIMARY KEY,
          owner TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS versions (
          id INTEGER PRIMARY KEY,
          email TEXT NOT NULL,
          number INTEGER NOT NULL,
          sha256 TEXT NOT NULL,
          sandbox TEXT NOT NULL DEFAULT '',
          plan TEXT NOT NULL DEFAULT '',
          size INTEGER NOT NULL DEFAULT 0,
          uploaded_by TEXT NOT NULL,
          message TEXT NOT NULL DEFAULT '',
          created_at TEXT NOT NULL,
          forced INTEGER NOT NULL DEFAULT 0,
          UNIQUE (email, number)
        );
        CREATE INDEX IF NOT EXISTS versions_sha ON versions (email, sha256);
        """)


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_user(name: str, role: str = "member", token: str = "") -> str:
    """建人。不给 token 就随机生成一个，返回明文（只在这一刻有）。"""
    plain = token or secrets.token_urlsafe(24)
    with db() as con:
        con.execute(
            "INSERT INTO users (name, token_hash, role, created_at) VALUES (?, ?, ?, ?)",
            (name, hash_token(plain), role, now()))
    return plain


def ensure_admin() -> None:
    with db() as con:
        if con.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]:
            return
    plain = os.environ.get("CCS_CLOUD_ADMIN_TOKEN") or secrets.token_urlsafe(24)
    create_user("admin", "admin", plain)
    print(f"首个管理员 token（只显示这一次）：{plain}", flush=True)  # noqa: T201 - 启动时打印一次


def user_of(token: str) -> sqlite3.Row | None:
    if not token:
        return None
    with db() as con:
        return con.execute("SELECT * FROM users WHERE token_hash = ?",
                           (hash_token(token),)).fetchone()


def latest(con: sqlite3.Connection, email: str) -> sqlite3.Row | None:
    return con.execute(
        "SELECT * FROM versions WHERE email = ? ORDER BY number DESC LIMIT 1",
        (email,)).fetchone()


# ---------------------------------------------------------------- 包检查


def inspect_bundle(body: bytes) -> dict:
    """解包，核对内容哈希，取出邮箱和方案。坏了就抛 ValueError。"""
    try:
        with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as tar:
            wanted = {m.name: m for m in tar.getmembers()
                      if m.name in ("bundle.json", "seed.json", "cookies.sqlite")}
            missing = {"bundle.json", "seed.json", "cookies.sqlite"} - set(wanted)
            if missing:
                msg = f"包里缺 {','.join(sorted(missing))}"
                raise ValueError(msg)
            info = json.loads(tar.extractfile(wanted["bundle.json"]).read())
            seed = tar.extractfile(wanted["seed.json"]).read()
            cookies = tar.extractfile(wanted["cookies.sqlite"]).read()
    except (tarfile.TarError, OSError, json.JSONDecodeError) as exc:
        msg = f"不是有效的沙盒包：{exc}"
        raise ValueError(msg) from exc
    if str(info.get("kind") or KIND) != KIND:
        msg = f"这个包不是 ccs 沙盒包（kind={info.get('kind')}）"
        raise ValueError(msg)
    email = str(info.get("email") or "").lower()
    if "@" not in email:
        msg = "包里没有邮箱，先在客户端 ccs refresh 一下"
        raise ValueError(msg)
    digest = hashlib.sha256()
    digest.update(seed)
    digest.update(b"\0")
    digest.update(cookies)
    digest.update(b"\0")
    digest.update(email.encode())
    info["email"] = email
    info["sha256"] = digest.hexdigest()
    return info


# ---------------------------------------------------------------- HTTP


class Handler(BaseHTTPRequestHandler):
    server_version = "ccs-cloud/1"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write(f"{now()} {self.address_string()} {fmt % args}\n")

    # -- 小工具

    def _send(self, status: int, body: bytes, kind: str = "application/json",
              extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: dict) -> None:
        self._send(status, json.dumps(payload, ensure_ascii=False).encode())

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _body(self) -> bytes:
        size = int(self.headers.get("Content-Length") or 0)
        if size <= 0:
            msg = "请求体是空的"
            raise ValueError(msg)
        if size > MAX_BODY:
            msg = f"包太大（{size} 字节，上限 {MAX_BODY}）"
            raise ValueError(msg)
        return self.rfile.read(size)

    def _auth(self):
        header = self.headers.get("Authorization") or ""
        user = user_of(header[7:].strip() if header.startswith("Bearer ") else "")
        if user is None:
            self._error(401, "token 不对或已吊销")
        return user

    # -- 路由

    def do_GET(self) -> None:  # noqa: PLR0911 - 路由就是按路径分叉
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.strip("/").split("/")]
        query = parse_qs(url.query)
        user = self._auth()
        if user is None:
            return None
        try:
            if parts == ["v1", "me"]:
                return self._json(200, {"name": user["name"], "role": user["role"]})
            if parts == ["v1", "accounts"]:
                return self._list_accounts()
            if len(parts) == PATH_ACCOUNT and parts[:2] == ["v1", "accounts"]:
                return self._download(parts[2], query)
            if len(parts) == PATH_ACTION and parts[:2] == ["v1", "accounts"] \
                    and parts[3] == "versions":
                return self._list_versions(parts[2])
            if len(parts) == PATH_ACTION and parts[:2] == ["v1", "accounts"] \
                    and parts[3] == "find":
                return self._find(parts[2], (query.get("sha256") or [""])[0])
            return self._error(404, "没有这个接口")
        except ValueError as exc:
            return self._error(400, str(exc))

    def do_POST(self) -> None:
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.strip("/").split("/")]
        query = parse_qs(url.query)
        user = self._auth()
        if user is None:
            return None
        try:
            if parts == ["v1", "users"]:
                return self._create_user(user)
            if len(parts) == PATH_ACTION and parts[:2] == ["v1", "accounts"] \
                    and parts[3] == "versions":
                return self._upload(parts[2], query, user)
            return self._error(404, "没有这个接口")
        except ValueError as exc:
            return self._error(400, str(exc))

    def do_DELETE(self) -> None:
        url = urlparse(self.path)
        parts = [unquote(p) for p in url.path.strip("/").split("/")]
        user = self._auth()
        if user is None:
            return None
        if len(parts) == PATH_ACCOUNT and parts[:2] == ["v1", "accounts"]:
            return self._delete(parts[2], user)
        return self._error(404, "没有这个接口")

    # -- 接口实现

    def _list_accounts(self) -> None:
        with db() as con:
            rows = con.execute("""
                SELECT a.email, a.owner, a.created_at, v.number, v.sha256,
                       v.plan, v.sandbox, v.size, v.created_at AS updated_at,
                       v.uploaded_by
                FROM accounts a
                JOIN versions v ON v.email = a.email
                 AND v.number = (SELECT MAX(number) FROM versions WHERE email = a.email)
                ORDER BY v.created_at DESC
            """).fetchall()
        self._json(200, {"accounts": [dict(row) for row in rows]})

    def _list_versions(self, email: str) -> None:
        with db() as con:
            rows = con.execute(
                "SELECT number, sha256, sandbox, plan, size, uploaded_by, message,"
                " created_at, forced FROM versions WHERE email = ? ORDER BY number",
                (email.lower(),)).fetchall()
        self._json(200, {"email": email.lower(), "versions": [dict(r) for r in rows]})

    def _find(self, email: str, sha: str) -> None:
        with db() as con:
            row = con.execute(
                "SELECT number FROM versions WHERE email = ? AND sha256 = ?"
                " ORDER BY number DESC LIMIT 1", (email.lower(), sha)).fetchone()
        if row is None:
            return self._error(404, "这份内容不在历史里")
        return self._json(200, {"version": row["number"]})

    def _download(self, email: str, query: dict) -> None:
        with db() as con:
            if query.get("version"):
                row = con.execute(
                    "SELECT * FROM versions WHERE email = ? AND number = ?",
                    (email.lower(), int(query["version"][0]))).fetchone()
            else:
                row = latest(con, email.lower())
        if row is None:
            return self._error(404, "云端没有这个账号")
        blob = DATA / "blobs" / f"{row['sha256']}.tar.gz"
        if not blob.is_file():
            return self._error(500, "包文件丢了")
        return self._send(200, blob.read_bytes(), "application/gzip", {
            "X-Version": str(row["number"]),
            "X-Sha256": row["sha256"],
            "X-Sandbox": quote(row["sandbox"]),
        })

    def _create_user(self, user) -> None:
        if user["role"] != "admin":
            return self._error(403, "只有管理员能建人")
        payload = json.loads(self._body())
        name = str(payload.get("name") or "").strip()
        if not name:
            return self._error(400, "要一个名字")
        try:
            token = create_user(name, str(payload.get("role") or "member"))
        except sqlite3.IntegrityError:
            return self._error(409, f"已经有这个人了: {name}")
        return self._json(200, {"name": name, "token": token})

    def _upload(self, email: str, query: dict, user) -> None:
        body = self._body()
        info = inspect_bundle(body)
        if info["email"] != email.lower():
            return self._error(400, f"路径写的邮箱是 {email}，包里是 {info['email']}")
        base = int((query.get("base") or ["0"])[0] or 0)
        message = str((query.get("message") or [""])[0])[:200]
        force = bool(int((query.get("force") or ["0"])[0] or 0))
        sha = info["sha256"]
        with _LOCK, db() as con:
            head = latest(con, email)
            if head is not None and head["sha256"] == sha:
                return self._json(200, {"version": head["number"], "sha256": sha,
                                        "unchanged": True})
            current = head["number"] if head else 0
            if head is None:
                number = 1
                con.execute("INSERT INTO accounts (email, owner, created_at)"
                            " VALUES (?, ?, ?)", (email, user["name"], now()))
            elif base == current:
                number = current + 1
            elif base == 0:
                old = con.execute(
                    "SELECT number FROM versions WHERE email = ? AND sha256 = ?"
                    " ORDER BY number DESC LIMIT 1", (email, sha)).fetchone()
                if old is not None:
                    return self._json(200, {"version": old["number"], "sha256": sha,
                                            "unchanged": True})
                if not force:
                    return self._conflict(head)
                number = current + 1
            elif force:
                number = current + 1
            else:
                return self._conflict(head)
            blob = DATA / "blobs" / f"{sha}.tar.gz"
            if not blob.is_file():
                blob.write_bytes(body)
            con.execute(
                "INSERT INTO versions (email, number, sha256, sandbox, plan, size,"
                " uploaded_by, message, created_at, forced)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (email, number, sha, str(info.get("sandbox") or ""),
                 str(info.get("plan_label") or info.get("plan") or ""), len(body),
                 user["name"], message, now(), int(force)))
        return self._json(200, {"version": number, "sha256": sha, "unchanged": False})

    def _conflict(self, head) -> None:
        return self._json(409, {
            "error": "别人更新过，先拉取最新版",
            "current_version": head["number"],
            "current_sha256": head["sha256"],
            "current_by": head["uploaded_by"],
            "current_at": head["created_at"],
        })

    def _delete(self, email: str, user) -> None:
        with _LOCK, db() as con:
            row = con.execute("SELECT * FROM accounts WHERE email = ?",
                              (email.lower(),)).fetchone()
            if row is None:
                return self._error(404, "云端没有这个账号")
            if user["role"] != "admin" and row["owner"] != user["name"]:
                return self._error(403, "只有上传者或管理员能删")
            con.execute("DELETE FROM versions WHERE email = ?", (email.lower(),))
            con.execute("DELETE FROM accounts WHERE email = ?", (email.lower(),))
        return self._json(200, {"deleted": email.lower()})


def main() -> int:
    parser = argparse.ArgumentParser(description="ccs 账号包同步服务")
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("CCS_CLOUD_PORT") or 8787))
    parser.add_argument("--data",
                        default=os.environ.get("CCS_CLOUD_DATA") or "/data")
    args = parser.parse_args()
    global DATA  # noqa: PLW0603 - 启动参数
    DATA = Path(args.data)
    init()
    ensure_admin()
    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)  # noqa: S104 - 容器里监听全部网卡
    print(f"ccs-cloud 在 :{args.port}，数据在 {DATA}", flush=True)  # noqa: T201 - 启动信息
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
