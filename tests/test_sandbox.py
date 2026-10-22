"""sandbox CLI 的冒烟测试：不碰网络，只测本地目录和参数校验。"""
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

CLI = Path(__file__).resolve().parent.parent / "sandbox"


class SandboxCliTests(unittest.TestCase):
    def run_cli(self, *args, root: Path, password: Path | None = None):
        env = dict(os.environ, SANDBOX_ROOT=str(root))
        if password is not None:
            env["SANDBOX_PASSWORD_FILE"] = str(password)
        return subprocess.run([str(CLI), *args], capture_output=True, text=True,
                              env=env, timeout=30)

    def test_new_then_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sandboxes"
            r = self.run_cli("new", "demo", "--exit", "cox", root=root)
            self.assertEqual(r.returncode, 0, r.stderr)
            meta = json.loads((root / "demo" / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["exit"], "cox")
            self.assertTrue((root / "demo" / "profile" / "seed.json").is_file())

            r = self.run_cli("list", root=root)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("demo", r.stdout)
            self.assertIn("cox", r.stdout)

    def test_new_rejects_unknown_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self.run_cli("new", "x", "--exit", "nope", root=Path(tmp))
            self.assertNotEqual(r.returncode, 0)

    def test_new_rejects_duplicate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sandboxes"
            self.assertEqual(self.run_cli("new", "demo", "--exit", "cox", root=root).returncode, 0)
            r = self.run_cli("new", "demo", "--exit", "cox", root=root)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("已存在", r.stderr)

    def test_new_accepts_proxy_url_and_keeps_it(self):
        """出口可以直接给地址：部署在任何地方都能用，不要求有 SSH。"""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "sandboxes"
            r = self.run_cli("new", "demo", "--exit", "socks5h://127.0.0.1:7897", root=root)
            self.assertEqual(r.returncode, 0, r.stderr)
            meta = json.loads((root / "demo" / "meta.json").read_text(encoding="utf-8"))
            self.assertEqual(meta["exit"], "socks5h://127.0.0.1:7897")

    def test_config_file_adds_exits(self):
        """配置文件里能加自己的出口（proxy 形态），doctor 要列出来。"""
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.json"
            cfg.write_text(json.dumps({"exits": {
                "mine": {"label": "自家代理", "proxy": "socks5h://10.0.0.1:1080"}}}),
                encoding="utf-8")
            env = dict(os.environ, SANDBOX_CONFIG=str(cfg))
            r = subprocess.run([str(CLI), "doctor"], capture_output=True, text=True,
                               env=env, timeout=30)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("mine", r.stdout)
            self.assertIn("10.0.0.1:1080", r.stdout)

    def test_doctor_reports_missing_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            r = self.run_cli("doctor", root=Path(tmp), password=Path(tmp) / "nope")
            self.assertEqual(r.returncode, 0, r.stderr)   # doctor 只报状态，不判失败
            self.assertIn("缺", r.stdout)


if __name__ == "__main__":
    unittest.main()
