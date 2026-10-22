"""单元测试：同进程中继、DoH 缓存、Sandbox 存储、report 表格。"""

import copy
import json
import socket
import sqlite3
import struct
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from ccs import config, paths, report  # noqa: E402
from ccs.browser.tasks import (  # noqa: E402
    _parse_csv,
    _parse_prompt_file,
    _pick_prompt,
    _read_prompt_dir,
    do_warmup,
    webrtc_public_ips,
    webrtc_verdict,
)
from ccs.network.dns_relay import DnsRelay  # noqa: E402
from ccs.network.doh import ATYP_DOMAIN, DohClient  # noqa: E402
from ccs.report import format_created  # noqa: E402
from ccs.sandbox import (  # noqa: E402
    bundle,
    store,
)
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
        self.assertIn("profile", lines[0])
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


class WarmupTests(unittest.TestCase):
    """养号的三态判断：没输入框 / 没发出去 / 发了没回 / 成功。"""

    class FakeKeyboard:
        def type(self, _text, delay=0):
            pass

        def press(self, _key):
            pass

    class FakePage:
        def __init__(self, texts, *, has_input=True):
            self.texts = list(texts)
            self.has_input = has_input
            self.keyboard = WarmupTests.FakeKeyboard()

        def wait_for_selector(self, _sel, timeout=0):
            if not self.has_input:
                msg = "没有输入框"
                raise RuntimeError(msg)
            return object()

        def click(self, _sel):
            pass

        def inner_text(self, _tag):
            if len(self.texts) > 1:
                return self.texts.pop(0)
            return self.texts[0]

    def cfg(self):
        cfg = copy.deepcopy(config.DEFAULT_CONFIG)
        cfg["warmup"].update({"poll_interval_s": 0, "send_confirm_s": 0.05,
                              "reply_wait_s": 0.05, "min_growth": 10})
        return cfg

    def test_no_input(self):
        page = self.FakePage(["x"], has_input=False)
        self.assertEqual(do_warmup(page, "你好", self.cfg()), "no_input")

    def test_message_never_appears(self):
        page = self.FakePage(["页面内容没有那句话"])
        self.assertEqual(do_warmup(page, "你好", self.cfg()), "not_sent")

    def test_sent_but_no_reply(self):
        page = self.FakePage(["你好", "你好"])
        self.assertEqual(do_warmup(page, "你好", self.cfg()), "no_reply")

    def test_sent_and_replied(self):
        page = self.FakePage(["你好", "你好", "你好" + "回复内容" * 20])
        self.assertEqual(do_warmup(page, "你好", self.cfg()), "ok")


class PromptFileTests(unittest.TestCase):
    """三种话术文件：一行一句 / --- 分段 / CSV。"""

    def test_lines(self):
        text = "# 注释\n你好\n\n再见\n"
        self.assertEqual(_parse_prompt_file(text), ["你好", "再见"])

    def test_blocks(self):
        text = "第一句\n---\n多行\n第二行\n"
        self.assertEqual(_parse_prompt_file(text), ["第一句", "多行\n第二行"])

    def test_csv_with_header_and_commas(self):
        text = 'prompt\n"你好，世界"\n"a,b"\n'
        self.assertEqual(_parse_csv(text), ["你好，世界", "a,b"])


class PromptDirTests(unittest.TestCase):
    """目录方式：一个文件一句，frontmatter 去掉，按文件名排序。"""

    def test_dir_with_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "02-b.md").write_text("第二句\n", encoding="utf-8")
            (folder / "01-a.md").write_text("---\nname: a\n---\n第一句\n", encoding="utf-8")
            self.assertEqual(_read_prompt_dir(folder), ["第一句", "第二句"])

    def test_dir_ignores_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / "empty.md").write_text("   \n", encoding="utf-8")
            (folder / "ok.txt").write_text("有用的一句\n", encoding="utf-8")
            self.assertEqual(_read_prompt_dir(folder), ["有用的一句"])


class PromptPickTests(unittest.TestCase):
    """挑话术：不重复上一句；daily 按日期轮换。"""

    def cfg(self, mode="random"):
        cfg = copy.deepcopy(config.DEFAULT_CONFIG)
        cfg["warmup"]["pick"] = mode
        return cfg

    def test_random_skips_last(self):
        prompts = ["甲", "乙", "丙"]
        with mock.patch("ccs.sandbox.store.read_account", return_value={"last_warmup_prompt": "甲"}):
            picked = {_pick_prompt("x", prompts, self.cfg()) for _ in range(40)}
        self.assertEqual(picked, {"乙", "丙"})

    def test_daily_is_stable_within_a_day(self):
        prompts = ["甲", "乙", "丙"]
        with mock.patch("ccs.sandbox.store.read_account", return_value={}):
            first = _pick_prompt("x", prompts, self.cfg("daily"))
            second = _pick_prompt("x", prompts, self.cfg("daily"))
        self.assertEqual(first, second)
        self.assertIn(first, prompts)


class DepsTests(unittest.TestCase):
    """系统库检查：缺哪个就给出对应的 apt 包名。"""

    def test_missing_lib_maps_to_package(self):
        from ccs.browser import deps  # noqa: PLC0415 - 只在这个测试里用

        with mock.patch.object(deps, "missing", return_value=["libasound.so.2"]):
            with mock.patch.object(deps, "_apt_has", side_effect=lambda pkg: pkg == "libasound2t64"):
                self.assertEqual(deps.packages(["libasound.so.2"]), ["libasound2t64"])
                self.assertIn("libasound.so.2", deps.hint())

    def test_nothing_missing_gives_no_hint(self):
        from ccs.browser import deps  # noqa: PLC0415 - 只在这个测试里用

        with mock.patch.object(deps, "missing", return_value=[]):
            self.assertEqual(deps.hint(), "")
            self.assertEqual(deps.packages([]), [])


class WebRtcTests(unittest.TestCase):
    """WebRTC 判断：mDNS 和私有地址不算，公网地址和出口比。"""

    def test_public_ips_only(self):
        cands = [
            "candidate:0 1 UDP 2122 abc.local 47243 typ host",
            "candidate:1 1 UDP 1686 104.202.107.35 58467 typ srflx",
            "candidate:2 1 TCP 2105 192.168.1.7 9 typ host",
            "candidate:3 1 UDP 1686 2001:4860::1 58467 typ srflx",
        ]
        self.assertEqual(webrtc_public_ips(cands), ["104.202.107.35", "2001:4860::1"])

    def test_verdicts(self):
        self.assertEqual(webrtc_verdict([], "104.202.107.35"), "没拿到公网 IP（不泄露）")
        same = ["candidate:1 1 UDP 1686 104.202.107.35 58467 typ srflx"]
        self.assertEqual(webrtc_verdict(same, "104.202.107.35"), "只有出口 IP，正常")
        leak = ["candidate:1 1 UDP 1686 1.2.3.4 58467 typ srflx"]
        self.assertIn("泄露", webrtc_verdict(leak, "104.202.107.35"))


class CreatedFormatTests(unittest.TestCase):
    """接口给 UTC，显示成本地时区、精确到分钟。"""

    def test_utc_to_local_minutes(self):
        self.assertRegex(format_created("2026-10-22T09:07:09.483901Z"),
                         r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$")

    def test_empty_and_bad(self):
        self.assertEqual(format_created(""), "-")
        self.assertEqual(format_created("不是时间"), "不是时间")


class BundleTests(unittest.TestCase):
    """导出/导入：种子和 claude.ai 的 cookie 必须一致，其它不带。"""

    def make_sandbox(self, root: Path, name: str, seed: int) -> None:
        profile = root / name / "profile"
        profile.mkdir(parents=True)
        (profile / "seed.json").write_text(json.dumps({"seed": seed}), encoding="utf-8")
        con = sqlite3.connect(profile / "cookies.sqlite")
        con.execute("CREATE TABLE moz_cookies (host TEXT, name TEXT, value TEXT)")
        con.executemany("INSERT INTO moz_cookies VALUES (?,?,?)", [
            (".claude.ai", "sessionKey", "sk-ant-test"),
            (".claude.ai", "anthropic-device-id", "dev-1"),
            (".example.com", "other", "x"),
        ])
        con.commit()
        con.close()
        (root / name / "meta.json").write_text(
            json.dumps({"name": name, "exit": "direct"}), encoding="utf-8")

    def test_roundtrip_keeps_seed_and_claude_cookies(self):

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sandboxes"
            root.mkdir()
            with mock.patch.object(paths, "ROOT", root):
                self.make_sandbox(root, "003", 42)
                out = Path(tmp) / "b.tar.gz"
                bundle.export_bundle("003", out, {"fonts": "western"}, {"screen.width": 2560})
                bundle.import_bundle(out, "003b", {"fonts": "western"})

                seed_a = json.loads((root / "003/profile/seed.json").read_text())
                seed_b = json.loads((root / "003b/profile/seed.json").read_text())
                self.assertEqual(seed_a, seed_b)

                con = sqlite3.connect(root / "003b/profile/cookies.sqlite")
                rows = con.execute("SELECT host, name FROM moz_cookies ORDER BY name").fetchall()
                con.close()
                self.assertEqual(rows, [(".claude.ai", "anthropic-device-id"),
                                        (".claude.ai", "sessionKey")])
                self.assertEqual(json.loads((root / "003b/meta.json").read_text())["fonts"],
                                 "western")
