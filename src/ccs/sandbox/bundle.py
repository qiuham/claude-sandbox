"""导出 / 导入沙盒身份：种子 + Claude 的 cookie + 指纹 pin。

只打包三样东西：
  bundle.json    元数据（版本、出口、pin、账号摘要）
  seed.json      指纹种子
  cookies.sqlite 只保留 claude.ai 的 cookie（含 sessionKey、anthropic-device-id）

不打包 profile 的其它内容（缓存、历史、storage），所以包很小、也不带无关站点数据。

"""

from __future__ import annotations

import json
import shutil
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path

from ccs import paths
from ccs.errors import die, say
from ccs.sandbox import store

BUNDLE_VERSION = 1
COOKIE_HOSTS = ("claude.ai", ".claude.ai")
REQUIRED = ("bundle.json", "seed.json", "cookies.sqlite")


def _profile(name: str) -> Path:
    return paths.profile_dir(name)


def _prune_cookies(source: Path, target: Path) -> int:
    """把 cookie 库复制一份，只留 claude.ai 的行；返回保留条数。"""
    con = sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True)
    try:
        con.execute("VACUUM INTO ?", (str(target),))
    finally:
        con.close()
    con = sqlite3.connect(target)
    try:
        placeholders = ",".join("?" * len(COOKIE_HOSTS))
        con.execute(f"DELETE FROM moz_cookies WHERE host NOT IN ({placeholders})",  # noqa: S608 - 占位符是常量个数，值走参数
                    COOKIE_HOSTS)
        con.commit()
        return int(con.execute("SELECT COUNT(*) FROM moz_cookies").fetchone()[0])
    finally:
        con.close()


def export_bundle(name: str, output: Path, cfg: dict, pin: dict) -> int:
    profile = _profile(name)
    seed_file = profile / "seed.json"
    cookies = profile / "cookies.sqlite"
    if not seed_file.is_file():
        die(f"{name} 还没有指纹种子（先 ccs web {name} 启动一次）")
    if not cookies.is_file():
        die(f"{name} 还没有 cookie（先登录一次）")

    account = store.read_account(name)
    meta = store.read_meta(name)
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        kept = _prune_cookies(cookies, tmpdir / "cookies.sqlite")
        shutil.copy2(seed_file, tmpdir / "seed.json")
        info = {
            "version": BUNDLE_VERSION,
            "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "sandbox": name,
            "exit": meta.get("exit", ""),
            "exit_ip": meta.get("exit_ip", ""),
            "pin": pin,
            "fonts": cfg.get("fonts", "western"),
            "email": account.get("email", ""),
            "org_id": account.get("org_id", ""),
            "cookies": kept,
        }
        (tmpdir / "bundle.json").write_text(
            json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        with tarfile.open(output, "w:gz") as tar:
            for item in REQUIRED:
                tar.add(tmpdir / item, arcname=item)

    size = output.stat().st_size
    say(f"已导出: {output.resolve()}（{size / 1024:.1f} KB，{kept} 条 cookie）")
    say(f"账号    : {account.get('email') or '-'}")
    say("注意    : 包里含 sessionKey，等于把这个号的登录权一起给出去了")
    return 0


def _read_bundle(tmpdir: Path) -> dict:
    info_file = tmpdir / "bundle.json"
    if not info_file.is_file():
        die("不是有效的沙盒包（缺 bundle.json）")
    info = json.loads(info_file.read_text(encoding="utf-8"))
    if int(info.get("version") or 0) > BUNDLE_VERSION:
        die(f"这个包是更新版本导出的（v{info['version']}），先升级 ccs")
    for item in REQUIRED:
        if not (tmpdir / item).is_file():
            die(f"包里缺 {item}")
    return info


def import_bundle(bundle: Path, name: str, cfg: dict) -> int:
    bundle = bundle.expanduser()      # 支持 ~/Downloads/xxx.tar.gz
    if not bundle.is_file():
        die(f"找不到文件: {bundle}")
    target = paths.sandbox_dir(name)
    if target.exists():
        die(f"{target} 已存在")

    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        with tarfile.open(bundle, "r:gz") as tar:
            tar.extractall(tmpdir)  # noqa: S202 - 只接受自己导出的包，文件名固定
        info = _read_bundle(tmpdir)

        target.mkdir(parents=True)
        profile = target / paths.PROFILE_DIR_NAME
        profile.mkdir()
        shutil.copy2(tmpdir / "seed.json", profile / "seed.json")
        shutil.copy2(tmpdir / "cookies.sqlite", profile / "cookies.sqlite")

    exit_spec = str(info.get("exit") or cfg.get("exit") or "direct")
    store.write_meta(name, {
        "name": name,
        "exit": exit_spec,
        "exit_ip": info.get("exit_ip", ""),
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "pin": info.get("pin") or {},
        "fonts": info.get("fonts") or "",
        "imported_from": str(info.get("sandbox") or ""),
    })
    if info.get("email") or info.get("org_id"):
        store.write_account(name, {
            "email": info.get("email", ""),
            "org_id": info.get("org_id", ""),
            "imported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })

    say(f"已导入: {name}（来自 {info.get('sandbox') or '未知沙盒'}）")
    say(f"账号    : {info.get('email') or '-'}")
    say(f"出口    : {exit_spec}")
    if info.get("exit_ip"):
        say(f"原出口IP: {info['exit_ip']}（首次启动会核对，不一致会拦住）")
    say(f"下一步  : ccs web {name}")
    return 0
