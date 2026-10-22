"""sandbox CLI 的冒烟测试：不碰网络，只测本地目录和参数校验。"""
import io
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

CLI = Path(__file__).resolve().parent.parent / "ccs"
SRC = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC))

from ccs import config as ccs_config  # noqa: E402


class SandboxCliTests(unittest.TestCase):
    def run_cli(self, *args, root: Path, config: Path | None = None, stdin: str | None = None):
        env = dict(os.environ, SANDBOX_ROOT=str(root))
        env["SANDBOX_CONFIG"] = str(config) if config else str(Path(root).parent / "nope.json")
        return subprocess.run([str(CLI), *args], capture_output=True, text=True,  # noqa: S603 - 跑的是本仓库的 CLI，参数固定
                              env=env, timeout=30, input=stdin, check=False)

    def write_config(self, path: Path, default_exit: str) -> Path:
        path.write_text(json.dumps({"exit": default_exit}), encoding="utf-8")
        return path

    def test_no_config_means_auto(self):
        """不配 = auto：找到本机代理就用，找不到才 direct。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sandboxes"
            r = self.run_cli("new", "demo", root=root)
            self.assertEqual(r.returncode, 0, r.stderr)
            meta = json.loads((root / "demo" / "meta.json").read_text(encoding="utf-8"))
            self.assertTrue(meta["exit"] == "direct" or meta["exit"].startswith("socks5h://"))

    def test_config_sets_default_exit(self):
        """配了 exit，新沙盒默认走它。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sandboxes"
            cfg = self.write_config(Path(tmp) / "config.json", "socks5h://127.0.0.1:7897")
            r = self.run_cli("new", "demo", root=root, config=cfg)
            self.assertEqual(r.returncode, 0, r.stderr)
            meta = json.loads((root / "demo" / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["exit"], "socks5h://127.0.0.1:7897")

            r = self.run_cli("doctor", root=root, config=cfg)
            self.assertIn("socks5h://127.0.0.1:7897", r.stdout)

    def test_explicit_exit_overrides_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sandboxes"
            cfg = self.write_config(Path(tmp) / "config.json", "socks5h://127.0.0.1:7897")
            r = self.run_cli("new", "demo", "--exit", "direct", root=root, config=cfg)
            self.assertEqual(r.returncode, 0, r.stderr)
            meta = json.loads((root / "demo" / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["exit"], "direct")

    def test_rejects_unknown_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self.run_cli("new", "x", "--exit", "cox", root=Path(tmp) / "s")
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("direct", r.stderr)

    def test_new_rejects_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sandboxes"
            self.assertEqual(self.run_cli("new", "demo", root=root).returncode, 0)
            r = self.run_cli("new", "demo", root=root)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("已存在", r.stderr)

    def test_status_and_set_exit(self):
        """status 看沙盒；set-exit 改出口（并清掉旧的出口 IP 记忆）。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sandboxes"
            self.assertEqual(self.run_cli("new", "t1", "--exit", "direct", root=root).returncode, 0)
            r = self.run_cli("status", "t1", "--json", root=root)
            info = json.loads(r.stdout)
            self.assertEqual(info["name"], "t1")
            self.assertEqual(info["exit"], "direct")
            self.assertIsInstance(info["seed"], int)

            r = self.run_cli("set-exit", "t1", "socks5h://1.2.3.4:1080", root=root)
            self.assertEqual(r.returncode, 0, r.stderr)
            meta = json.loads((root / "t1" / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["exit"], "socks5h://1.2.3.4:1080")

            r = self.run_cli("set-exit", "t1", "cox", root=root)   # 名字已经不支持
            self.assertNotEqual(r.returncode, 0)

    def test_ls_json_lists_slots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sandboxes"
            self.run_cli("new", "a", root=root)
            self.run_cli("new", "b", "--exit", "socks5h://10.0.0.1:1080", root=root)
            r = self.run_cli("ls", "--json", root=root)
            data = json.loads(r.stdout)
            self.assertEqual([x["name"] for x in data["slots"]], ["a", "b"])
            self.assertEqual(data["slots"][1]["exit"], "socks5h://10.0.0.1:1080")

    def test_rm_needs_confirmation_unless_forced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sandboxes"
            self.run_cli("new", "t1", root=root)
            r = self.run_cli("rm", "t1", root=root, stdin="no\n")
            self.assertNotEqual(r.returncode, 0)
            self.assertTrue((root / "t1").is_dir())
            r = self.run_cli("rm", "t1", "--force", root=root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse((root / "t1").exists())

    def test_doctor_says_direct_when_unconfigured(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self.run_cli("doctor", root=Path(tmp) / "s", config=Path(tmp) / "nope.json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("auto（new 时自动找本机代理）", r.stdout)

    def test_doctor_finds_dns_relay(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self.run_cli("doctor", root=Path(tmp) / "s", config=Path(tmp) / "nope.json")
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertRegex(r.stdout, r"DNS 中继\s*: 有")

    def test_screen_defaults_to_1080p(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self.run_cli("doctor", root=Path(tmp) / "s", config=Path(tmp) / "nope.json")
            self.assertRegex(r.stdout, r"屏幕\s*: 1920x1080 @ 1x")

    def test_screen_config_and_auto(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.json"
            cfg.write_text(json.dumps({"screen": "1920x1080"}), encoding="utf-8")
            r = self.run_cli("doctor", root=Path(tmp) / "s", config=cfg)
            self.assertRegex(r.stdout, r"屏幕\s*: 1920x1080")

            cfg.write_text(json.dumps({"screen": "auto"}), encoding="utf-8")
            r = self.run_cli("doctor", root=Path(tmp) / "s", config=cfg)
            self.assertIn("auto", r.stdout)

    def test_screen_rejects_garbage(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.json"
            cfg.write_text(json.dumps({"screen": "big"}), encoding="utf-8")
            r = self.run_cli("doctor", root=Path(tmp) / "s", config=cfg)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("screen 写法不对", r.stderr)


class ConfigTomlTests(unittest.TestCase):
    def load_toml(self, text: str) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ccs.toml"
            path.write_text(text, encoding="utf-8")
            with mock.patch.dict(os.environ, {"SANDBOX_CONFIG": str(path)}):
                return ccs_config.load_config()

    def test_toml_defaults_cover_migrated_constants(self):
        """原先写死的常量都在默认配置里。"""
        cfg = ccs_config.DEFAULT_CONFIG
        self.assertEqual(len(cfg["warmup"]["prompts"]), 4)
        self.assertEqual(cfg["dns"]["doh_ip"], "1.1.1.1")
        self.assertEqual(cfg["dns"]["listen_host"], "127.0.0.1")
        self.assertEqual((cfg["dns"]["ttl_min"], cfg["dns"]["ttl_max"]), (30, 300))
        self.assertEqual(cfg["paths"]["root"], "~/sandboxes")
        timeouts = cfg["browser"]["timeouts"]
        for key, value in (("close_grace_s", 5),
                           ("preflight_s", 15), ("page_ms", 45000),
                           ("goto_ms", 90000), ("warmup_s", 90)):
            self.assertEqual(timeouts[key], value, key)
        prefs = cfg["browser"]["prefs"]
        self.assertEqual(prefs["network.trr.mode"], 3)
        self.assertEqual(prefs["browser.startup.page"], 0)
        self.assertIn("Microsoft YaHei", cfg["fonts_markers"])

    def test_roundtrip_keeps_dotted_pref_keys(self):
        """写出去再读回来：带点的偏好名不能变成嵌套表。"""
        cfg = {
            "exit": "socks5h://127.0.0.1:7897",
            "dpr": 1.5,
            "strict_ip": True,
            "browser": {
                "prefs": {"browser.cache.disk.enable": False, "network.trr.mode": 3},
                "timeouts": {"page_ms": 45000},
            },
            "warmup": {"prompts": ["你好", "早上好"]},
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ccs.toml"
            ccs_config.write_toml(cfg, path)
            got = tomllib.loads(path.read_text(encoding="utf-8"))

        self.assertEqual(got["browser"]["prefs"]["browser.cache.disk.enable"], False)
        self.assertEqual(got["browser"]["prefs"]["network.trr.mode"], 3)
        self.assertEqual(got["browser"]["timeouts"]["page_ms"], 45000)
        self.assertEqual(got["warmup"]["prompts"], ["你好", "早上好"])

    def test_toml_overrides_all_sections(self):
        cfg = self.load_toml(
            'exit = "socks5h://127.0.0.1:7897"\n'
            'screen = "2560x1440"\n'
            "dpr = 1.25\n"
            "strict_ip = false\n"
            'fonts_markers = ["SimSun"]\n'
            "[dns]\n"
            'doh_ip = "9.9.9.9"\n'
            "ttl_min = 10\n"
            "[warmup]\n"
            'prompts = ["你好"]\n'
            "[browser.timeouts]\n"
            "page_ms = 1234\n"
            "close_grace_s = 7\n"
            "[browser.prefs]\n"
            '"network.trr.mode" = 2\n'
        )
        self.assertEqual(cfg["exit"], "socks5h://127.0.0.1:7897")
        self.assertEqual(cfg["screen"], "2560x1440")
        self.assertEqual(cfg["dpr"], 1.25)
        self.assertFalse(cfg["strict_ip"])
        self.assertEqual(cfg["fonts_markers"], ["SimSun"])
        self.assertEqual(cfg["dns"]["doh_ip"], "9.9.9.9")
        self.assertEqual(cfg["dns"]["ttl_min"], 10)
        self.assertEqual(cfg["dns"]["ttl_max"], 300)       # 没写的保留默认
        self.assertEqual(cfg["warmup"]["prompts"], ["你好"])
        self.assertEqual(cfg["browser"]["timeouts"]["page_ms"], 1234)
        self.assertEqual(cfg["browser"]["timeouts"]["goto_ms"], 90000)
        self.assertEqual(cfg["browser"]["prefs"]["network.trr.mode"], 2)
        self.assertFalse(cfg["browser"]["prefs"]["browser.cache.disk.enable"])

    def test_explicit_json_is_read_without_writing_anything(self):
        """显式指定旧 JSON 时照读，不迁移、不写文件（测试和临时配置走这条路）。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"exit": "direct", "screen": "auto"}),
                            encoding="utf-8")
            err = io.StringIO()
            with mock.patch.dict(os.environ, {"SANDBOX_CONFIG": str(path)}), \
                    redirect_stderr(err):
                cfg = ccs_config.load_config()
            files = sorted(p.name for p in Path(tmp).iterdir())
        self.assertEqual(cfg["screen"], "auto")
        self.assertEqual(files, ["config.json"])
        self.assertEqual(err.getvalue(), "")

    def test_toml_config_used_by_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sandboxes"
            cfg = Path(tmp) / "ccs.toml"
            cfg.write_text(
                'exit = "socks5h://10.1.2.3:1080"\n'
                '[paths]\n'
                f'root = "{root}"\n',
                encoding="utf-8",
            )
            r = subprocess.run([str(CLI), "new", "demo"], capture_output=True, text=True,  # noqa: S603 - 跑的是本仓库的 CLI，参数固定
                               env=dict(os.environ, SANDBOX_CONFIG=str(cfg)), timeout=30,
                               check=False)
            self.assertEqual(r.returncode, 0, r.stderr)
            meta = json.loads((root / "demo" / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["exit"], "socks5h://10.1.2.3:1080")


if __name__ == "__main__":
    unittest.main()
