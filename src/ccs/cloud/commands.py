"""云端的命令：login / ls / push / pull / log / revert / user。"""

from __future__ import annotations

import getpass
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from ccs import paths, ui
from ccs.cloud import api, state
from ccs.errors import die, say
from ccs.sandbox import bundle, store
from ccs.sandbox.runtime import load_runtime

HTTP_CONFLICT = 409


@dataclass
class Sync:
    """一次云端命令要用到的东西：地址、令牌、本机配置、指纹 pin。"""

    url: str
    token: str
    cfg: dict
    pin: dict = field(default_factory=dict)


def _session() -> tuple[str, str]:
    conf = state.load()
    if not conf["url"] or not conf["token"]:
        die("还没登录云端：先 ccs cloud login <服务地址>")
    return conf["url"], conf["token"]


def _lines(headers: list[str], rows: list[list[str]]) -> list[str]:
    """没有 Rich 时的纯文本表格。"""
    widths = [max(len(h), *(len(r[i]) for r in rows)) if rows else len(h)
              for i, h in enumerate(headers)]
    out = ["  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))]
    out.extend("  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))
               for row in rows)
    return out


def _show(headers: list[str], rows: list[list[str]]) -> None:
    ui.emit_table(headers, rows, fallback=_lines(headers, rows))


def _email_of(ref: str) -> str:
    """ref 可以是邮箱，也可以是本地沙盒名。"""
    if "@" in ref:
        return ref.lower()
    mail = state.mail_of(ref)
    if not mail:
        die(f"{ref} 里没有邮箱（先 ccs refresh {ref}）")
    return mail


def _local_hash(name: str, cfg: dict) -> str:
    """本地这份内容的内容哈希；没登录过的沙盒返回空串。"""
    profile = paths.profile_dir(name)
    if not (profile / "seed.json").is_file() or not (profile / "cookies.sqlite").is_file():
        return ""
    try:
        _, info = bundle.pack(name, cfg, {})
    except SystemExit:            # die 是退出整条命令的，这里只跳过这一件
        return ""
    return str(info.get("sha256") or "")


def _remember(name: str, url: str, email: str, result: dict, sha: str) -> None:
    state.write_state(name, {
        "url": url.rstrip("/"),
        "email": email,
        "version": int(result.get("version") or 0),
        "sha256": sha,
        "synced_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })


# ---------------------------------------------------------------- login


def cmd_cloud_login(args) -> int:
    url = str(args.url or state.load()["url"]).rstrip("/")
    if not url:
        die("要给服务地址，例如 ccs cloud login https://sync.example.com")
    token = str(args.token or "").strip()
    if not token:
        token = getpass.getpass("token（粘贴后回车）: ").strip()
    if not token:
        die("没给 token")
    try:
        me = api.whoami(url, token)
    except api.CloudError as exc:
        die(f"登录失败：{exc}")
    if not state.save(url, token):
        die(f"写不进 {state.CONFIG_FILE}")
    say(ui.kv("已登录", f"{url}（{me.get('name')}，{me.get('role')}）"))
    return 0


def cmd_cloud_whoami(_args) -> int:
    url, token = _session()
    try:
        me = api.whoami(url, token)
    except api.CloudError as exc:
        die(str(exc))
    say(ui.kv("服务", url))
    say(ui.kv("我是", f"{me.get('name')}（{me.get('role')}）"))
    return 0


# ---------------------------------------------------------------- ls


def cmd_cloud_ls(_args) -> int:
    url, token = _session()
    try:
        items = api.accounts(url, token)
    except api.CloudError as exc:
        die(str(exc))
    if not items:
        say("云端还没有账号")
        return 0
    headers = ["邮箱", "版本", "方案", "更新", "上传者", "本地"]
    rows = [[str(item.get("email") or "-"),
             f"v{item.get('number')}",
             str(item.get("plan") or "-"),
             str(item.get("updated_at") or "-"),
             str(item.get("uploaded_by") or "-"),
             state.sandbox_for(str(item.get("email") or ""), url) or "-"]
            for item in items]
    _show(headers, rows)
    return 0


# ---------------------------------------------------------------- push


def _push_one(name: str, syn: Sync, message: str, *, force: bool) -> tuple[bool, str]:
    content, info = bundle.pack(name, syn.cfg, syn.pin)
    email = str(info.get("email") or "").lower()
    if "@" not in email:
        return False, f"{name}: 没抓到邮箱，先 ccs refresh {name}"
    sha = str(info["sha256"])
    local = state.read_state(name)
    same_service = (str(local.get("email") or "").lower() == email
                    and str(local.get("url") or "").rstrip("/") == syn.url.rstrip("/"))
    base_version = int(local.get("version") or 0) if same_service else 0
    try:
        if not base_version:
            # 本地没有版本号：按内容问服务端"我是哪一版"
            base_version = api.find(syn.url, syn.token, email, sha) or 0
        result = api.upload(syn.url, syn.token, email, sha=sha,
                            base_version=base_version, message=message,
                            force=force, body=content)
    except api.CloudError as exc:
        if exc.status == HTTP_CONFLICT:
            payload = exc.payload
            return False, (f"{email}: 云端已是 v{payload.get('current_version')}"
                           f"（{payload.get('current_by')}，{payload.get('current_at')}）；"
                           f"先 ccs cloud pull {email} 看一眼，或用 --force")
        return False, f"{email}: {exc}"
    _remember(name, syn.url, email, result, sha)
    if result.get("unchanged"):
        return True, f"{email}: 和云端 v{result.get('version')} 一样，没有新内容"
    return True, f"{email}: 已推成 v{result.get('version')}"


def bundle_pin(cfg: dict) -> dict:
    """导出用的指纹 pin；字体清单在本地重新生成，不写进包里。"""
    from ccs.browser.prefs import build_pin  # noqa: PLC0415 - 只在推的时候需要

    pin = build_pin(cfg)
    pin.pop("font.manifest", None)
    return pin


def cmd_cloud_push(args) -> int:
    cfg = load_runtime()
    url, token = _session()
    syn = Sync(url, token, cfg, bundle_pin(cfg))
    names = args.names or [s.name for s in store.list_sandboxes()]
    if not names:
        say("本地没有沙盒")
        return 0
    failed = 0
    for name in names:
        ok, line = _push_one(name, syn, str(args.message or ""),
                             force=bool(args.force))
        say(line)
        failed += 0 if ok else 1
    return 1 if failed else 0


# ---------------------------------------------------------------- pull


def _pull_one(ref: str, syn: Sync, as_name: str, *, force: bool) -> tuple[bool, str]:
    email = _email_of(ref)
    try:
        content, head = api.download(syn.url, syn.token, email)
    except api.CloudError as exc:
        return False, f"{email}: {exc}"
    with tempfile.TemporaryDirectory() as tmp:
        tmpdir = Path(tmp)
        info = bundle.read_bundle(content, tmpdir)
        name = as_name or state.sandbox_for(email, syn.url) \
            or str(info.get("sandbox") or "") or email.split("@")[0]
        target = paths.sandbox_dir(name)
        if target.exists() and not force:
            local_sha = _local_hash(name, syn.cfg)
            synced = str(state.read_state(name).get("sha256") or "")
            if local_sha and local_sha not in (synced, head["sha256"]):
                return False, (f"{name}: 本地有没同步的改动"
                               f"（{local_sha[:8]}，云端 {head['sha256'][:8]}）；"
                               "确认要覆盖就加 --force")
        final = bundle.unpack(tmpdir, info, name, syn.cfg, force=True)
    _remember(final, syn.url, email, {"version": head["version"]}, head["sha256"])
    return True, f"{email}: 拉到 {final}（v{head['version']}）"


def cmd_cloud_pull(args) -> int:
    cfg = load_runtime()
    url, token = _session()
    syn = Sync(url, token, cfg)
    refs = args.refs
    if not refs:
        try:
            refs = [str(a.get("email")) for a in api.accounts(url, token)]
        except api.CloudError as exc:
            die(str(exc))
    if not refs:
        say("云端还没有账号")
        return 0
    failed = 0
    for ref in refs:
        ok, line = _pull_one(ref, syn, str(args.name or ""), force=bool(args.force))
        say(line)
        failed += 0 if ok else 1
    return 1 if failed else 0


# ---------------------------------------------------------------- log / revert


def cmd_cloud_log(args) -> int:
    url, token = _session()
    email = _email_of(args.ref)
    try:
        items = api.versions(url, token, email)
    except api.CloudError as exc:
        die(str(exc))
    if not items:
        say(f"云端没有 {email}")
        return 0
    headers = ["版本", "时间", "上传者", "说明", "内容哈希"]
    rows = []
    for item in items:
        note = str(item.get("message") or "")
        if not note and item.get("forced"):
            note = "强制推"
        rows.append([f"v{item.get('number')}", str(item.get("created_at") or "-"),
                     str(item.get("uploaded_by") or "-"), note or "-",
                     str(item.get("sha256") or "")[:8]])
    _show(headers, rows)
    return 0


def cmd_cloud_revert(args) -> int:
    url, token = _session()
    email = _email_of(args.ref)
    try:
        _, head = api.download(url, token, email)
        content, old = api.download(url, token, email, version=int(args.to))
        result = api.upload(url, token, email, sha=old["sha256"],
                            base_version=head["version"],
                            message=f"revert to v{old['version']}", force=False,
                            body=content)
    except api.CloudError as exc:
        die(str(exc))
    local = state.sandbox_for(email, url)
    if local:
        _remember(local, url, email, result, old["sha256"])
    say(ui.kv("已回退", f"{email}: v{old['version']} 的内容推成 v{result.get('version')}"))
    return 0


# ---------------------------------------------------------------- user


def cmd_cloud_user(args) -> int:
    url, token = _session()
    try:
        result = api.create_user(url, token, args.name)
    except api.CloudError as exc:
        die(str(exc))
    say(ui.kv("已建人", str(result.get("name"))))
    say(ui.kv("token", str(result.get("token") or "")))
    say("这个 token 只显示这一次，交给本人后本项目不保留明文")
    return 0
