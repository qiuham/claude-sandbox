"""终端输出：结构化行 + 纯文本兜底。

只生成文字行，由调用方决定怎么打印（say 会同时写日志）。
"""

from __future__ import annotations

from datetime import datetime


def ls_lines(rows: list[dict]) -> list[str]:
    """ls 的表头和每行；字段顺序固定。"""
    lines = [f"{'profile':<8} {'出口 IP':<16} {'账号':<28} {'档位':<7} {'对话':<4} {'注册':<16} 组织"]
    for row in rows:
        acct = row.get("account") or {}
        email = acct.get("email") or "-"
        created = format_created(acct.get("account_created_at") or "")
        org = acct.get("org_id") or "-"
        lines.append(f"{row.get('name', '-'):<8} {row.get('exit_ip', '-'):<16} {email:<28} "
                     f"{plan_label(acct):<7} {warmup_count(acct)!s:<4} {created:<16} {org}")
    return lines


def status_lines(info: dict) -> list[str]:
    """status 的每个字段；顺序固定。"""
    seed = info.get("seed")
    return [
        f"名称      : {info.get('name')}",
        f"目录      : {info.get('dir')}",
        f"出口      : {info.get('exit', '-')}",
        f"出口 IP   : {exit_ip_label(info)}",
        f"指纹种子  : {seed if seed is not None else '-'}",
        f"profile   : {info.get('profile_bytes', 0) / 1024 / 1024:.1f} MB",
        f"创建时间  : {info.get('created_at', '-')}",
    ]


def account_lines(account: dict) -> list[str]:
    """key 命令里的账号两行；顺序固定。"""
    return [
        f"账 号: {account.get('email') or '-'}",
        f"组织  : {account.get('org_id') or '-'}",
    ]


PLAN_TIERS = {
    "default_claude_ai": "免费",
    "default_claude_pro": "Pro",
    "default_claude_max_5x": "Max 5x",
    "default_claude_max_20x": "Max 20x",
}


def plan_label(acct: dict) -> str:
    """把接口里的 tier 换成能一眼看懂的名字。"""
    raw = str(acct.get("plan_label") or acct.get("plan") or "").strip()
    if not raw:
        return "-"
    return PLAN_TIERS.get(raw, raw)


def plan_style(label: str) -> str:
    if label.startswith("Max"):
        return "bold magenta"
    if label == "Pro":
        return "bold cyan"
    return "dim"


def warmup_count(account: dict) -> int:
    """养号次数。老数据没有计数但有最近一次时间，按 1 次算。"""
    if account.get("warmup_count"):
        return int(account["warmup_count"])
    return 1 if account.get("last_warmup_at") else 0


def format_created(value: str) -> str:
    """接口给的是 UTC ISO，显示成本地时区、精确到分钟。"""
    if not value:
        return "-"
    text = str(value).strip()
    try:
        # Python 3.11+ 的 fromisoformat 直接认 Z 结尾
        moment = datetime.fromisoformat(text)
        return moment.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return text[:16]


def exit_ip_label(info: dict) -> str:
    """出口 IP 加上当前节点名：104.202.107.35（[vless]美国Wave1）。"""
    ip = str(info.get("exit_ip") or "")
    label = str(info.get("exit_label") or "")
    if not ip:
        return "-（启动一次后记录）"
    return f"{ip}（{label}）" if label else ip


def ls_rows(rows: list[dict]) -> tuple[list[str], list[list[str]]]:
    """ls 的表头和单元格，给 ui.emit_table 用。"""
    headers = ["profile", "出口 IP", "账号", "档位", "对话", "注册", "组织"]
    cells = []
    for row in rows:
        acct = row.get("account") or {}
        cells.append([
            str(row.get("name", "-")),
            exit_ip_label(row) if row.get("exit_ip") else "-",
            str(acct.get("email") or "-"),
            plan_label(acct),
            str(warmup_count(acct)),
            format_created(acct.get("account_created_at") or ""),
            str(acct.get("org_id") or "-"),
        ])
    return headers, cells


def ls_styles(cells: list[list[str]]) -> list[list[str]]:
    """每格的颜色：档位突出，组织和缺失值压暗。"""
    return [[
        "bold" if cell[0] != "-" else "dim",
        "" if cell[1] != "-" else "dim",
        "" if cell[2] != "-" else "dim",
        plan_style(cell[3]),
        "dim" if cell[4] in ("0", "-") else "",
        "dim" if cell[5] == "-" else "",
        "dim",
    ] for cell in cells]


def status_styles(pairs: list[tuple[str, str]]) -> list[str]:
    """status 每行的颜色：出口 IP 青色，档位按等级，空值压暗。"""
    styles = []
    for key, value in pairs:
        if value in ("-", "") or str(value).startswith("-（"):
            styles.append("dim")
        elif key == "出口 IP":
            styles.append("cyan")
        elif key == "档位":
            styles.append(plan_style(str(value)))
        elif key == "指纹种子":
            styles.append("dim")
        else:
            styles.append("")
    return styles


def account_pairs(account: dict) -> list[tuple[str, str]]:
    """status 里的账号段：档位、计费、组织、注册。"""
    if not account:
        return [("账号", "-（登录一次后记录）")]
    pairs = [("账号", str(account.get("email") or "-")),
             ("档位", plan_label(account))]
    if account.get("billing_type"):
        pairs.append(("计费", str(account["billing_type"])))
    pairs.append(("组织", str(account.get("org_id") or "-")))
    if account.get("account_created_at"):
        pairs.append(("注册", format_created(str(account["account_created_at"]))))
    if warmup_count(account):
        last = str(account.get("last_warmup_at") or "")
        pairs.append(("对话", f"{warmup_count(account)} 次" + (f" · 最近 {last}" if last else "")))
    state = str(account.get("alive_state") or "")
    if state:
        label = {"ok": "正常", "invalid": "会话失效", "restricted": "账号受限"}.get(state, state)
        checked = str(account.get("alive_checked_at") or "")
        pairs.append(("状态", f"{label} · {checked}" if checked else label))
    return pairs


def status_pairs(info: dict) -> list[tuple[str, str]]:
    """status 的字段对，给 ui.emit_pairs 用。"""
    seed = info.get("seed")
    return [
        ("名称", str(info.get("name"))),
        ("目录", str(info.get("dir"))),
        ("出口", str(info.get("exit", "-"))),
        ("出口 IP", exit_ip_label(info)),
        ("指纹种子", str(seed if seed is not None else "-")),
        ("profile", f"{info.get('profile_bytes', 0) / 1024 / 1024:.1f} MB"),
        ("创建时间", str(info.get("created_at", "-"))),
    ]
