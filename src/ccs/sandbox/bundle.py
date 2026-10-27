"""导出 / 导入沙盒身份：种子 + Claude 的 cookie + 指纹 pin。

只打包三样东西：
  bundle.json    元数据（版本、出口、pin、账号摘要、内容哈希）
  seed.json      指纹种子
  cookies.sqlite 只保留 claude.ai 的 cookie（含 sessionKey、anthropic-device-id）

不打包 profile 的其它内容（缓存、历史、storage），所以包很小、也不带无关站点数据。

内容哈希只算三样：种子、cookie、邮箱。打出来的包换了压缩参数哈希也不变，
云端靠它判断"这两份是不是同一份登录态"。
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import sqlite3
import tarfile
import tempfile
import time
from pathlib import Path

from ccs import paths, ui
from ccs.browser import process
from ccs.errors import die, say
from ccs.network import geo
from ccs.sandbox import store

BUNDLE_VERSION = 2
KIND = "ccs-sandbox"
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


def content_hash(seed: bytes, cookies: bytes, email: str) -> str:
    """登录态的内容哈希：种子 + cookie + 邮箱。"""
    digest = hashlib.sha256()
    digest.update(seed)
    digest.update(b"\0")
    digest.update(cookies)
    digest.update(b"\0")
    digest.update((email or "").lower().encode())
    return digest.hexdigest()


def _read_files(name: str) -> tuple[bytes, bytes, dict, dict]:
    profile = _profile(name)
    seed_file = profile / "seed.json"
    cookies = profile / "cookies.sqlite"
    if not seed_file.is_file():
        die(f"{name} 还没有指纹种子（先 ccs web {name} 启动一次）")
    if not cookies.is_file():
        die(f"{name} 还没有 cookie（先登录一次）")
    return seed_file.read_bytes(), cookies, store.read_account(name), store.read_meta(name)


def pack(name: str, cfg: dict, pin: dict) -> tuple[bytes, dict]:
    """把沙盒打成 tar.gz 字节。返回 (内容, 包信息)。"""
    seed, cookies, account, meta = _read_files(name)
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        kept = _prune_cookies(cookies, tmpdir / "cookies.sqlite")
        pruned = (tmpdir / "cookies.sqlite").read_bytes()
        info = {
            "version": BUNDLE_VERSION,
            "kind": KIND,
            "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "sandbox": name,
            "exit": meta.get("exit", ""),
            "exit_ip": meta.get("exit_ip", ""),
            "label": meta.get("exit_label", ""),
            "pin": pin,
            "fonts": cfg.get("fonts", "western"),
            "email": account.get("email", ""),
            "display_name": account.get("display_name", ""),
            "org_id": account.get("org_id", ""),
            "org_name": account.get("org_name", ""),
            "plan": account.get("plan", ""),
            "plan_label": account.get("plan_label", ""),
            "account_created_at": account.get("account_created_at", ""),
            "cookies": kept,
            "sha256": content_hash(seed, pruned, account.get("email", "")),
        }
        (tmpdir / "seed.json").write_bytes(seed)
        (tmpdir / "bundle.json").write_text(
            json.dumps(info, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for item in REQUIRED:
                tar.add(tmpdir / item, arcname=item)
    return buf.getvalue(), info


def read_bundle(content: bytes, tmpdir: Path) -> dict:
    """解包到 tmpdir，返回 bundle.json 的内容。"""
    try:
        with tarfile.open(fileobj=io.BytesIO(content), mode="r:gz") as tar:
            tar.extractall(tmpdir)  # noqa: S202 - 只接受自己导出的包，文件名固定
    except (tarfile.TarError, OSError) as exc:
        die(f"不是有效的沙盒包：{exc}")
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


def _free_name(base: str) -> str:
    """重名时加 -2、-3，直到空出来。"""
    candidate = base or "imported"
    index = 1
    while paths.sandbox_dir(candidate).exists():
        index += 1
        candidate = f"{base or 'imported'}-{index}"
    return candidate


def unpack(tmpdir: Path, info: dict, name: str, cfg: dict, *, force: bool) -> str:
    """把包里的身份写到沙盒 name。返回实际用的名字。"""
    target = paths.sandbox_dir(name)
    exists = (target / paths.META_FILE_NAME).is_file()
    if exists and not force:
        name = _free_name(name)
        target = paths.sandbox_dir(name)
    profile = target / paths.PROFILE_DIR_NAME
    profile.mkdir(parents=True, exist_ok=True)
    if exists and force and process.profile_pids(profile):
        say(f"先关掉 {name} 开着的浏览器")
        process.stop_browser(profile, grace=5)
    shutil.copy2(tmpdir / "seed.json", profile / "seed.json")
    shutil.copy2(tmpdir / "cookies.sqlite", profile / "cookies.sqlite")
    previous = store.read_meta(name) if exists else {}
    exit_spec = str(info.get("exit") or cfg.get("exit") or "direct")
    exit_ip = str(info.get("exit_ip") or "")
    label = geo.lookup(exit_ip) or str(info.get("label") or "")
    store.write_meta(name, {
        "name": name,
        "exit": previous.get("exit") or exit_spec,
        "exit_ip": exit_ip,
        "exit_label": label,
        "created_at": previous.get("created_at") or time.strftime("%Y-%m-%d %H:%M:%S"),
        "pin": info.get("pin") or {},
        "fonts": info.get("fonts") or "",
        "imported_from": str(info.get("sandbox") or ""),
    })
    wanted = ("email", "display_name", "org_id", "org_name", "plan", "plan_label",
              "account_created_at")
    picked = {key: info.get(key, "") for key in wanted if info.get(key)}
    if picked:
        picked["imported_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        store.write_account(name, {**store.read_account(name), **picked})
    return name


def export_bundle(name: str, output: Path, cfg: dict, pin: dict) -> int:
    content, info = pack(name, cfg, pin)
    output.write_bytes(content)
    say(ui.kv("已导出", f"{output.resolve()}（{len(content) / 1024:.1f} KB，"
                     f"{info['cookies']} 条 cookie）"))
    say(ui.kv("账号", f"{info.get('email') or '-'}"))
    say(ui.kv("内容哈希", info["sha256"][:16]))
    say(ui.kv("注意", "包里含 sessionKey，等于把这个号的登录权一起给出去了"))
    return 0


def import_bundle(path: Path, name: str, cfg: dict, *, force: bool = False) -> int:
    path = path.expanduser()      # 支持 ~/Downloads/xxx.tar.gz
    if not path.is_file():
        die(f"找不到文件: {path}")
    content = path.read_bytes()
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        info = read_bundle(content, tmpdir)
        final = unpack(tmpdir, info, name or str(info.get("sandbox") or ""), cfg,
                       force=force)
    say(ui.kv("已导入", f"{final}（来自 {info.get('sandbox') or '未知沙盒'}）"))
    say(ui.kv("账号", f"{info.get('email') or '-'}"))
    say(ui.kv("出口", str(info.get("exit") or cfg.get("exit") or "direct")))
    say(ui.kv("下一步", f"ccs web {final}"))
    return 0
