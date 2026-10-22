"""sandbox CLI 的冒烟测试：不碰网络，只测本地目录和参数校验。"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

CLI = Path(__file__).resolve().parent.parent / "ccs"


class SandboxCliTests(unittest.TestCase):
    def run_cli(self, *args, root: Path, config: Path | None = None, stdin: str | None = None):
        env = dict(os.environ, SANDBOX_ROOT=str(root))
        env["SANDBOX_CONFIG"] = str(config) if config else str(Path(root).parent / "nope.json")
        return subprocess.run([str(CLI), *args], capture_output=True, text=True,
                              env=env, timeout=30, input=stdin)

    def write_config(self, path: Path, default_exit: str) -> Path:
        path.write_text(json.dumps({"exit": default_exit}), encoding="utf-8")
        return path

    def test_no_config_means_direct(self):
        """不配就是 direct。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sandboxes"
            r = self.run_cli("new", "demo", root=root)
            self.assertEqual(r.returncode, 0, r.stderr)
            meta = json.loads((root / "demo" / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["exit"], "direct")

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
            self.assertEqual(self.run_cli("new", "t1", root=root).returncode, 0)
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
            self.assertIn("没配置 = direct", r.stdout)


if __name__ == "__main__":
    unittest.main()
