"""单元测试：同进程中继、DoH 缓存、Sandbox 存储、report 表格。"""

import copy
import socket
import struct
import sys
import tempfile
import threading
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from ccs import config, paths, report  # noqa: E402
from ccs.network.dns_relay import DnsRelay  # noqa: E402
from ccs.network.doh import ATYP_DOMAIN, DohClient  # noqa: E402
from ccs.sandbox import store  # noqa: E402
from ccs.sandbox.model import Sandbox  # noqa: E402


def relay_config() -> dict:
    cfg = copy.deepcopy(config.DEFAULT_CONFIG)
    cfg["dns"]["listen_port"] = 0
    cfg["browser"]["timeouts"]["relay_stop_s"] = 2
    return cfg


def recvn(sock, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            break
        buf += chunk
    return buf


class FakeUpstream:
    """最小的 SOCKS5 上游：接受一次 CONNECT，记录目标，然后回显数据。"""

    def __init__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.seen = []
        threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        try:
            conn, _ = self.sock.accept()
        except OSError:
            return
        with conn:
            recvn(conn, 3)                    # 版本 + 方法数 + 方法
            conn.sendall(b"\x05\x00")
            head = recvn(conn, 4)
            atyp = head[3]
            if atyp == 1:
                host = socket.inet_ntoa(recvn(conn, 4))
            elif atyp == ATYP_DOMAIN:
                host = recvn(conn, recvn(conn, 1)[0]).decode()
            else:
                host = "?"
            recvn(conn, 2)
            self.seen.append((atyp, host))
            conn.sendall(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
            conn.sendall(b"echo:" + conn.recv(5))

    def close(self):
        self.sock.close()


class FakeDoh:
    def __init__(self):
        self.names = []

    def resolve(self, name):
        self.names.append(name)
        return ["1.2.3.4"]


class DnsRelayTests(unittest.TestCase):
    def test_start_stop_releases_port_and_is_repeatable(self):
        relay = DnsRelay("socks5://127.0.0.1:9", relay_config())
        addr = relay.start()
        self.assertRegex(addr, r"^socks5://127\.0\.0\.1:\d+$")
        port = int(addr.rsplit(":", 1)[1])
        self.assertEqual(relay.start(), addr)

        relay.stop()
        relay.stop()                      # 重复 stop 不能抛错
        self.assertEqual(relay.addr, "")

        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(("127.0.0.1", port))   # 端口确实释放了

    def test_non_connect_command_gets_0x07(self):
        relay = DnsRelay("socks5://127.0.0.1:9", relay_config())
        addr = relay.start()
        port = int(addr.rsplit(":", 1)[1])
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=3) as client:
                client.sendall(b"\x05\x01\x00")
                self.assertEqual(recvn(client, 2), b"\x05\x00")
                client.sendall(b"\x05\x02\x00\x01")   # BIND：不支持
                self.assertEqual(recvn(client, 4)[:2], b"\x05\x07")
        finally:
            relay.stop()

    def test_domain_is_resolved_and_upstream_sees_only_ip(self):
        upstream = FakeUpstream()
        relay = DnsRelay(f"socks5://127.0.0.1:{upstream.port}", relay_config())
        fake_doh = FakeDoh()
        relay.doh = fake_doh
        addr = relay.start()
        port = int(addr.rsplit(":", 1)[1])
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=3) as client:
                client.sendall(b"\x05\x01\x00")
                self.assertEqual(recvn(client, 2), b"\x05\x00")
                name = b"example.com"
                client.sendall(b"\x05\x01\x00\x03" + bytes([len(name)]) + name
                               + struct.pack("!H", 443))
                self.assertEqual(recvn(client, 10)[:2], b"\x05\x00")
                client.sendall(b"hello")
                self.assertEqual(recvn(client, 10), b"echo:hello")
        finally:
            relay.stop()
            upstream.close()
        self.assertEqual(fake_doh.names, ["example.com"])
        self.assertEqual(upstream.seen, [(1, "1.2.3.4")])   # ATYP=1，只有 IP


class DohClientTests(unittest.TestCase):
    def test_cache_hit_skips_second_query(self):
        client = DohClient("socks5://127.0.0.1:9", relay_config())
        calls = []

        def fake_query(name, qtype):
            calls.append((name, qtype))
            return {"Answer": [{"type": 1, "data": "93.184.216.34", "TTL": 60}]}

        client._query = fake_query  # noqa: SLF001 - 测试要替换私有查询方法，模拟 DoH 返回

        self.assertEqual(client.resolve("example.com"), ["93.184.216.34"])
        self.assertEqual(client.resolve("example.com"), ["93.184.216.34"])
        self.assertEqual(calls, [("example.com", "A")])


class SandboxStoreTests(unittest.TestCase):
    def setUp(self):
        self._old_root = paths.ROOT
        self._tmp = tempfile.TemporaryDirectory()
        paths.configure({"paths": {"root": self._tmp.name}}, Path(self._tmp.name) / "x.toml")

    def tearDown(self):
        paths.ROOT = self._old_root
        self._tmp.cleanup()

    def test_meta_and_account_roundtrip(self):
        root = Path(self._tmp.name)
        (root / "demo").mkdir()
        sandbox = Sandbox(name="demo", exit="socks5h://10.0.0.1:1080",
                          created_at="2026-10-22 10:00:00", exit_ip="1.2.3.4")
        store.write_meta("demo", sandbox)

        self.assertEqual(store.read_meta("demo"), sandbox.to_meta())
        self.assertEqual(store.load("demo"), sandbox)
        self.assertEqual([s.name for s in store.list_sandboxes()], ["demo"])

        info = {"email": "a@example.com", "session_key": "secret"}
        self.assertTrue(store.write_account("demo", info))
        self.assertEqual(store.read_account("demo"), info)
        self.assertEqual((root / "demo" / "account.json").stat().st_mode & 0o777, 0o600)


class ReportTests(unittest.TestCase):
    def test_ls_and_status_tables(self):
        lines = report.ls_lines([
            {"name": "demo", "exit_ip": "1.2.3.4",
             "account": {"email": "a@example.com",
                         "account_created_at": "2026-10-22T10:00:00Z",
                         "org_id": "org-1"}},
        ])
        self.assertIn("名字", lines[0])
        self.assertLess(lines[0].index("出口 IP"), lines[0].index("账号"))
        for text in ("demo", "1.2.3.4", "a@example.com", "2026-10-22", "org-1"):
            self.assertIn(text, lines[1])

        status = report.status_lines({
            "name": "demo", "dir": "/tmp/demo", "exit": "direct",  # noqa: S108 - 只是报表展示路径，不创建文件
            "exit_ip": None, "seed": 42, "profile_bytes": 2 * 1024 * 1024,
            "created_at": "2026-10-22 10:00:00",
        })
        self.assertEqual(status[0], "名称      : demo")
        self.assertIn("-（启动一次后记录）", status[3])
        self.assertIn("42", status[4])
        self.assertIn("2.0 MB", status[5])


if __name__ == "__main__":
    unittest.main()
