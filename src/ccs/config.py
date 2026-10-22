"""配置：TOML 读取、默认值、旧 JSON 迁移、基本校验。

新配置：~/.config/claude-sandbox/ccs.toml（见仓库根的 ccs.toml.example）。
旧配置：~/.config/claude-sandbox/config.json。旧文件存在时照常读取，并提示已迁移。
环境变量 SANDBOX_CONFIG 可以指定任意一个配置文件（JSON 或 TOML）。
环境变量 SANDBOX_ROOT 覆盖 [paths].root。
"""

from __future__ import annotations

import copy
import json
import os
import re
import sys
import tomllib
from pathlib import Path

from ccs.errors import die

DEFAULT_CONFIG_DIR = Path("~/.config/claude-sandbox").expanduser()
DEFAULT_CONFIG_FILE = DEFAULT_CONFIG_DIR / "ccs.toml"
LEGACY_CONFIG_FILE = DEFAULT_CONFIG_DIR / "config.json"

# 沙盒的浏览器偏好：目标只有一个 —— DNS 不经过本机、也不经过 Clash 的解析器。
# 做法：DoH only（mode=3），DoH 请求本身走同一条代理，所以解析发生在出口侧。
SANDBOX_PREFS = {
    # SOCKS 把域名交给代理解析（显式写上，不依赖默认值）
    "network.proxy.socks_remote_dns": True,
    # DoH only：不做系统 DNS 回退，避免"DoH 失败就漏给人"
    "network.trr.mode": 3,
    "network.trr.uri": "https://cloudflare-dns.com/dns-query",
    "network.trr.custom_uri": "https://cloudflare-dns.com/dns-query",
    "network.trr.bootstrapAddress": "1.1.1.1",
    # 崩溃报告器会弹一个窗口等人点，无人值守时永久卡住。直接关掉。
    "browser.crashReports.unsubmittedCheck.enabled": False,
    "browser.tabs.crashReporting.sendReport": False,
    "breakpad.reportURL": "",
    # 不写磁盘缓存：profile 不再长这 41MB。
    # 登录态在 storage/ 里，不受影响；内存缓存照用，同一会话不重复下载。
    "browser.cache.disk.enable": False,
    "browser.cache.disk.capacity": 0,
    "browser.cache.disk_cache_ssl": False,
    # 关掉所有"提前解析"的通道
    "network.dns.disablePrefetch": True,
    "network.prefetch-next": False,
    "network.predictor.enabled": False,
    "network.http.speculative-parallel-limit": 0,
    # IPv6 也会绕过代理做 DNS
    "network.dns.disableIPv6": True,
    # HTTP/3 走 UDP，SOCKS 代理默认不带 UDP —— 关掉，别给它绕路的机会
    "network.http.http3.enabled": False,
    "network.http.altsvc.enabled": False,
    "network.http.altsvc.oe": False,
    # WebRTC 的 STUN 走 UDP，SOCKS 只代理 TCP —— 默认会拿真实公网 IP 去问 STUN。
    # 只允许代理候选：候选列表为空，ipleak 的 WebRTC 一栏看不到任何 IP。
    "media.peerconnection.ice.proxy_only": True,
    # 双保险：连本机地址候选也不要（默认已经用 mDNS 名字）
    "media.peerconnection.ice.no_host": True,
}

NO_DIALOG_PREFS = {
    "browser.sessionstore.resume_from_crash": False,
    "browser.sessionstore.max_resumed_crashes": 0,
    "browser.startup.page": 0,
    "browser.shell.checkDefaultBrowser": False,
    "browser.aboutwelcome.enabled": False,
    "datareporting.policy.dataSubmissionEnabled": False,
    "toolkit.telemetry.enabled": False,
    "browser.tabs.warnOnClose": False,
    "dom.disable_beforeunload": True,
}

WARM_PROMPTS = [
    "你好，用一句话介绍一下你自己",
    "帮我把这句话改得更简洁：今天的工作已经全部完成了",
    "用一句话解释什么是云服务器",
    "写一句适合早上发的问候语",
]

# 简繁中文字体：默认去掉。检测工具把"装了中文字体"当中国用户信号，
# 而且一台美国/日本的 Windows 本来就不一定装这些。
CJK_FONT_MARKERS = (
    "Microsoft YaHei", "NSimSun", "SimSun", "Microsoft JhengHei",
    "MingLiU", "PMingLiU", "msyh", "simsun",
)

# 屏幕分辨率的合理范围（像素）：太小可能是写错，太大超出常见显示器。
SCREEN_WIDTH_MIN = 640
SCREEN_WIDTH_MAX = 7680
SCREEN_HEIGHT_MIN = 480
SCREEN_HEIGHT_MAX = 4320

DEFAULT_CONFIG = {
    # 出口没配置 = direct；屏幕默认 1080p（窗口物理宽度 = 面板宽度，必须放得进显示器）；
    # 字体默认去掉简繁中文（和"美国/日本 Windows"对不上）
    "exit": "",
    "screen": "1920x1080",
    "dpr": 1,
    "fonts": "western",
    "fit_window": True,
    "color_scheme": "dark",
    "humanize_s": 1.5,          # 单次鼠标动作的时间上限（秒），越大越慢越自然
    "strict_ip": True,
    "dns": {
        "doh_ip": "1.1.1.1",
        "doh_name": "cloudflare-dns.com",
        "doh_path": "/dns-query",
        "ttl_min": 30,
        "ttl_max": 300,
        "listen_host": "127.0.0.1",
        "listen_port": 0,          # 0 = 每次启动挑一个空闲端口
        "connect_timeout_s": 15,
        "client_timeout_s": 30,
    },
    "warmup": {
        "prompts": WARM_PROMPTS,
        "type_delay_ms": 240,      # 每个字的基准间隔（毫秒），真人大概这个量级
        "type_jitter_ms": 70,
        "think_every_chars": 18,   # 每打这么多字停一下，像在想
        "think_pause_min_ms": 250,
        "think_pause_max_ms": 700,
        "click_dwell_min_ms": 120,  # 点击前停顿
        "click_dwell_max_ms": 400,      # 上下浮动
        "send_confirm_s": 30,
        "reply_wait_s": 90,
        "poll_interval_s": 2,
        "min_growth": 120,
    },
    "paths": {
        "root": "~/sandboxes",
        "profile_dir": "profile",
        "meta_file": "meta.json",
        "account_file": "account.json",
        "log_file": "ccs.log",
    },
    "browser": {
        "prefs": {**NO_DIALOG_PREFS, **SANDBOX_PREFS},
        "timeouts": {
            "preflight_s": 15,
            "preflight_process_s": 25,
            "page_ms": 45000,
            "goto_ms": 90000,
            "selector_ms": 15000,
            "warmup_s": 90,
            "harvest_interval_s": 45,
            "close_grace_s": 5,
            "close_join_s": 5,
            "close_join_extra_s": 2,
            "relay_stop_s": 1,
            "pgrep_s": 5,
            "kill_poll_s": 0.2,
            "kill_settle_s": 0.3,
            "run_sleep_s": 1,
        },
    },
    "fonts_markers": list(CJK_FONT_MARKERS),
}

_SOURCE_PATH: Path | None = None
_MIGRATED_FROM: Path | None = None


def config_file_path() -> Path:
    """显式配置路径优先，否则用新的 ccs.toml。"""
    raw = os.environ.get("SANDBOX_CONFIG")
    if raw:
        return Path(raw).expanduser()
    return DEFAULT_CONFIG_FILE


def source_path() -> Path:
    """本次实际读取的配置文件；没有文件时是预期路径。"""
    return _SOURCE_PATH or config_file_path()


def migrated_from() -> Path | None:
    return _MIGRATED_FROM


def _read(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        die(f"配置文件读不了: {path}: {exc}")
    try:
        if path.suffix.lower() != ".toml":
            try:
                data = json.loads(text)
            except json.JSONDecodeError:
                data = tomllib.loads(text)
        else:
            data = tomllib.loads(text)
    except (json.JSONDecodeError, tomllib.TOMLDecodeError) as exc:
        die(f"配置文件读不了: {path}: {exc}")
    if not isinstance(data, dict):
        die(f"配置文件读不了: {path}: 顶层必须是表（section）")
    return data


def _section(cfg: dict, key: str) -> dict:
    value = cfg.get(key)
    if not isinstance(value, dict):
        die(f"配置 {key} 必须是表（section）")
    return value


def _merge_flat(cfg: dict, data: dict) -> None:
    value = str(data.get("exit") or "").strip()
    if value:
        cfg["exit"] = value
    screen = str(data.get("screen") or "").strip()
    if screen:
        cfg["screen"] = screen
    fonts = str(data.get("fonts") or "").strip()
    if fonts:
        cfg["fonts"] = fonts
    if data.get("dpr") not in (None, ""):
        cfg["dpr"] = data["dpr"]
    if "fit_window" in data:
        cfg["fit_window"] = bool(data["fit_window"])
    if "strict_ip" in data:
        cfg["strict_ip"] = bool(data["strict_ip"])
    markers = data.get("fonts_markers")
    if isinstance(markers, list):
        cfg["fonts_markers"] = [str(x) for x in markers]


def _merge(cfg: dict, data: dict) -> None:
    _merge_flat(cfg, data)
    for key in ("dns", "warmup", "paths"):
        if key in data:
            if not isinstance(data[key], dict):
                die(f"配置 {key} 必须是表（section）")
            cfg[key].update(data[key])
    browser = data.get("browser")
    if browser is None:
        return
    if not isinstance(browser, dict):
        die("配置 browser 必须是表（section）")
    if "prefs" in browser:
        if not isinstance(browser["prefs"], dict):
            die("配置 browser.prefs 必须是表（section）")
        cfg["browser"]["prefs"].update(browser["prefs"])
    if "timeouts" in browser:
        if not isinstance(browser["timeouts"], dict):
            die("配置 browser.timeouts 必须是表（section）")
        cfg["browser"]["timeouts"].update(browser["timeouts"])


def _toml_key(key: str) -> str:
    """裸键只留字母数字下划线横线；浏览器偏好的名字带点，必须加引号。

    不加引号时 TOML 会把 `browser.cache.disk.enable` 解释成嵌套表，
    读回来就变成 {"browser": {"cache": ...}}，偏好全丢。
    """
    return key if re.fullmatch(r"[A-Za-z0-9_-]+", key) else f'"{key}"'


def _toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    msg = f"TOML 写不了这种值: {value!r}"
    raise TypeError(msg)


def write_toml(cfg: dict, path: Path) -> None:
    """把配置写成 TOML。只支持本项目用到的类型：标量、字符串列表、两级表。"""
    lines: list[str] = []
    for key, value in cfg.items():
        if not isinstance(value, dict):
            lines.append(f"{_toml_key(key)} = {_toml_value(value)}")
    for section, body in cfg.items():
        if not isinstance(body, dict):
            continue
        lines.append("")
        lines.append(f"[{section}]")
        sub_tables = {}
        for key, value in body.items():
            if isinstance(value, dict):
                sub_tables[key] = value
            else:
                lines.append(f"{_toml_key(key)} = {_toml_value(value)}")
        for key, value in sub_tables.items():
            lines.append("")
            lines.append(f"[{section}.{key}]")
            for k, v in value.items():
                lines.append(f"{_toml_key(k)} = {_toml_value(v)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def migrate_legacy_json(cfg: dict, source: Path) -> Path | None:
    """把旧 config.json 落成 ccs.toml，旧文件改名，避免每次启动都提示。"""
    target = DEFAULT_CONFIG_FILE
    try:
        write_toml(cfg, target)
        source.rename(source.with_name(source.name + ".migrated"))
    except OSError as exc:
        print(f"配置: 迁移失败（{exc}），继续用旧文件", file=sys.stderr, flush=True)  # noqa: T201 - 迁移提示写到 stderr
        return None
    return target


def load_config() -> dict:
    """读配置。没有文件就用默认值；旧 config.json 迁移成 TOML 后不再提示。"""
    global _SOURCE_PATH, _MIGRATED_FROM  # noqa: PLW0603 - 进程级单例，启动时设置一次
    cfg = copy.deepcopy(DEFAULT_CONFIG)
    explicit = bool(os.environ.get("SANDBOX_CONFIG"))
    path = config_file_path()
    chosen: Path | None = None
    legacy = False

    if explicit:
        # 显式指定就照读，不迁移（测试和临时配置都走这条路）
        if path.is_file():
            chosen = path
    elif path.is_file():
        chosen = path
    elif LEGACY_CONFIG_FILE.is_file():
        chosen = LEGACY_CONFIG_FILE
        legacy = True

    if chosen is not None:
        _merge(cfg, _read(chosen))
    if legacy:
        target = migrate_legacy_json(cfg, LEGACY_CONFIG_FILE)
        if target is not None:
            chosen = target
            print(  # noqa: T201 - 迁移提示写到 stderr
                f"配置: 已迁移到 {target}（旧的改名为 {LEGACY_CONFIG_FILE.name}.migrated）",
                file=sys.stderr,
                flush=True,
            )

    _SOURCE_PATH = chosen or path
    _MIGRATED_FROM = None
    return cfg


def parse_screen(spec) -> dict:
    """`"3840x2160"` → 指纹 pin；`"auto"` → 不 pin，用种子自己的。"""
    spec = str(spec or "auto").strip().lower()
    if spec in ("", "auto"):
        return {}
    try:
        width, height = (int(x) for x in spec.replace("×", "x").split("x", 1))
    except ValueError:
        die(f"screen 写法不对，要 3840x2160 这种，收到 {spec!r}")
    if not (SCREEN_WIDTH_MIN <= width <= SCREEN_WIDTH_MAX
            and SCREEN_HEIGHT_MIN <= height <= SCREEN_HEIGHT_MAX):
        die(f"screen 超出合理范围: {spec!r}")
    return {"screen.width": width, "screen.height": height}


def parse_dpr(value) -> float:
    """缩放比例。引擎只测过 1、1.25、1.5、2 这四档。"""
    try:
        dpr = float(value)
    except (TypeError, ValueError):
        die(f"dpr 要写数字，收到 {value!r}")
    if dpr not in (1.0, 1.25, 1.5, 2.0):
        die(f"dpr 只能写 1、1.25、1.5、2，收到 {value}")
    return dpr
