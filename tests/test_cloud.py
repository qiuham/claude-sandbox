"""云端服务的冒烟测试：起一个真的服务，走真实的 HTTP。

只测版本规则（不覆盖、冲突、按内容认版本）和不登录就拒绝。
不碰 claude.ai，也不需要浏览器。
"""

from __future__ import annotations

import io
import json
import os
import socket
import subprocess
import sys
import tarfile
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER = ROOT / "server" / "ccs_cloud.py"
ADMIN = "test-admin-token"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def make_bundle(email: str, seed: bytes, cookie: bytes) -> bytes:
    """按导出包的格式做一个最小包。"""
    info = {"version": 2, "kind": "ccs-sandbox", "sandbox": "006",
            "email": email, "plan": "default_claude_pro"}
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, data in (("bundle.json", json.dumps(info).encode()),
                           ("seed.json", seed),
                           ("cookies.sqlite", cookie)):
            member = tarfile.TarInfo(name)
            member.size = len(data)
            tar.addfile(member, io.BytesIO(data))
    return buf.getvalue()


class CloudServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        cls.port = free_port()
        env = dict(os.environ, CCS_CLOUD_DATA=cls.tmp.name,
                   CCS_CLOUD_ADMIN_TOKEN=ADMIN)
        cls.proc = subprocess.Popen(  # noqa: S603 - 跑本仓库的服务端，参数固定
            [sys.executable, str(SERVER), "--port", str(cls.port), "--data", cls.tmp.name],
            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.base = f"http://127.0.0.1:{cls.port}"
        for _ in range(100):
            try:
                socket.create_connection(("127.0.0.1", cls.port), timeout=0.2).close()
            except OSError:
                time.sleep(0.05)
            else:
                return
        cls.tearDownClass()
        msg = "服务端没起来"
        raise RuntimeError(msg)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.proc.terminate()
        cls.proc.wait(timeout=5)
        cls.tmp.cleanup()

    def call(self, method: str, path: str, *, token: str = ADMIN,
             body: bytes | None = None) -> tuple[int, dict]:
        req = urllib.request.Request(  # noqa: S310 - 本地测试服务
            self.base + path, data=body, method=method)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310 - 本地测试服务
                return resp.status, json.loads(resp.read() or b"{}")
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def upload(self, email: str, body: bytes, **query) -> tuple[int, dict]:
        parts = "&".join(f"{k}={v}" for k, v in query.items())
        path = f"/v1/accounts/{email}/versions" + (f"?{parts}" if parts else "")
        return self.call("POST", path, body=body)

    def test_needs_token(self) -> None:
        """没 token 一律 401。"""
        status, _ = self.call("GET", "/v1/accounts", token="")
        self.assertEqual(status, 401)

    def test_version_rules(self) -> None:
        """同一份内容不重复存；过期基线拒绝；按内容能认出是哪一版。"""
        email = "rules@example.com"
        first = make_bundle(email, b"seed-1", b"cookie-1")
        status, payload = self.upload(email, first, base=0)
        self.assertEqual((status, payload["version"]), (200, 1))

        # 同一份内容再推一次：不新增版本
        status, payload = self.upload(email, first, base=0)
        self.assertEqual((status, payload["version"], payload["unchanged"]),
                         (200, 1, True))

        second = make_bundle(email, b"seed-1", b"cookie-2")
        status, payload = self.upload(email, second, base=1)
        self.assertEqual((status, payload["version"]), (200, 2))

        # 基线过期：拒绝，不写
        third = make_bundle(email, b"seed-1", b"cookie-3")
        status, payload = self.upload(email, third, base=1)
        self.assertEqual(status, 409)
        self.assertEqual(payload["current_version"], 2)

        # 本地没版本号，但这份内容就是 v2
        status, payload = self.upload(email, second, base=0)
        self.assertEqual((status, payload["version"], payload["unchanged"]),
                         (200, 2, True))

        # 本地没版本号，内容确实是新的：不给 --force 就拒绝
        status, _ = self.upload(email, third, base=0)
        self.assertEqual(status, 409)
        status, payload = self.upload(email, third, base=0, force=1)
        self.assertEqual((status, payload["version"]), (200, 3))

        # 历史都在
        status, payload = self.call("GET", f"/v1/accounts/{email}/versions")
        self.assertEqual([v["number"] for v in payload["versions"]], [1, 2, 3])

        # 按内容查版本
        status, payload = self.call("GET", f"/v1/accounts/{email}/find?sha256=nope")
        self.assertEqual(status, 404)

    def test_mail_must_match_path(self) -> None:
        """路径写的邮箱和包里的对不上就拒绝。"""
        body = make_bundle("a@example.com", b"s", b"c")
        status, _ = self.upload("b@example.com", body, base=0)
        self.assertEqual(status, 400)

    def test_bad_bundle_rejected(self) -> None:
        """不是包就 400，不会写坏数据。"""
        status, _ = self.call("POST", "/v1/accounts/x@example.com/versions?base=0",
                              body=b"not a tar")
        self.assertEqual(status, 400)

    def test_admin_creates_user(self) -> None:
        """只有管理员能建人；新 token 能用。"""
        status, payload = self.call("POST", "/v1/users",
                                    body=json.dumps({"name": "alice"}).encode())
        self.assertEqual(status, 200)
        token = payload["token"]
        status, me = self.call("GET", "/v1/me", token=token)
        self.assertEqual((status, me["name"]), (200, "alice"))
        status, _ = self.call("POST", "/v1/users",
                              body=json.dumps({"name": "bob"}).encode(), token=token)
        self.assertEqual(status, 403)


if __name__ == "__main__":
    unittest.main()
