# ccs（claude-sandbox）

一个 Claude 注册/养号用的沙盒。一个沙盒 = 一个独立 profile + 一个固定出口 + 一份固定指纹。
手工版给人在窗口里点，同一套脚本加 `--headless` 就能给自动化用。

## 命令

| 命令 | 作用 |
|---|---|
| `ccs new <名字> [--exit 地址\|direct]` | 新建沙盒 |
| `ccs ls [--json]`（别名 `list`） | 列出沙盒 |
| `ccs status <名字> [--json]` | 沙盒详情（出口、种子、profile 大小） |
| `ccs web <名字>` | 打开窗口，手工注册 |
| `ccs check <名字>` | 无头自检（出口 IP / WebRTC） |
| `ccs set-exit <名字> <地址\|direct>` | 改沙盒出口 |
| `ccs rm <名字> [--force]` | 删除沙盒（含 profile） |
| `ccs doctor` | 检查环境和配置 |

## 设计

| 层 | 做法 |
|---|---|
| 网络 | 浏览器只连指定的代理地址，不依赖系统代理、不依赖 TUN。DNS 由代理侧解析 |
| 出口 | **不配 = auto（自动找本机代理）；配了 = 走指定代理** |
| 浏览器 | invisible_playwright（改过的 Firefox）：指纹种子、时区/语言、WebRTC 都与出口一致 |
| 身份 | 每个沙盒独立 profile 目录；种子写在 `profile/seed.json`，以后一直用它 |

## 出口

三种：

| 写法 | 含义 |
|---|---|
| 不配置 = `auto`（默认） | 启动时找本机代理；找到就用，找不到才 direct |
| `--exit direct` | 完全不用代理，走原生环境（出口由本机的 TUN/透明代理决定） |
| 配置里写 `exit` / `--exit socks5h://…` | 显式走指定代理 |

`auto` 会挨个试 `127.0.0.1` 的 7897（Clash Verge）、7890（Clash）、10808（v2ray）、1080，
WSL 的 NAT 模式下还会试默认网关。做一次 SOCKS5 握手再 CONNECT 一个域名，通了才算数。
新建沙盒时找到哪个就写进该沙盒的 `meta.json`，以后不再变。

配置文件 `~/.config/claude-sandbox/ccs.toml`（见 `ccs.toml.example`）：

```toml
exit = "socks5h://127.0.0.1:7897"
```

旧版的 `~/.config/claude-sandbox/config.json` 仍然能读，启动时会提示"已迁移"。

* `exit` 可以是任何 `socks5h://` / `http://` 地址；出口优先用 `socks5h://`。
* 命令行 `--exit <地址>` 或 `--exit direct` 优先于配置。
* `ccs doctor` 会列出检测到的本机代理，并用 STUN 量一次 UDP 出口。
* `ccs ls` / `ccs status` / 启动时会显示出口 IP 的标签：
  按 IP 查地理位置（缓存 7 天）。配置里写 `exit_label = "JP-1"` 可自定义。

## 手工版（WSL / 本机）

1. 一键安装：

```
make setup            # 使用这套：只装运行依赖（没有 ruff 这类开发工具）
make dev              # 开发这套：连 ruff 一起装
```

没装 make 的机器，直接跑脚本，效果一样：

```
./install.sh          # 等于 make setup
./install.sh --dev    # 等于 make dev
./install.sh --auto   # 没装 uv 时直接装，不询问
./install.sh --no-deps   # 不装系统库，只打印手动命令
```

它做五件事：`uv sync` 建环境装依赖 → 下载浏览器引擎（约 260 MB）→
缺系统库时自动用 apt 装（先打印完整命令）→
把 `ccs` 软链到 `~/.local/bin` → 跑一次 `ccs doctor` 自检。

不想让脚本装系统库时，加 `--no-deps`，或设环境变量 `SKIP_DEPS=1`。

引擎版本钉在 `pyproject.toml` 里（`invisible-playwright==0.35.10`）。
**指纹一致依赖引擎版本一致**，对方装同样版本，同一个 seed 才能还原同样的指纹。

2. 建沙盒、启动、看状态：

```
ccs doctor
ccs new profile1                 # 有配置就走配置的出口，没有就 direct
ccs new profile2 --exit direct    # 显式不用代理
ccs ls                            # 列出所有沙盒
ccs status profile1               # 出口、种子、profile 大小
ccs web profile1                  # 打开窗口
ccs set-exit profile1 socks5h://127.0.0.1:7897
ccs rm profile2                   # 删除前会要求输入 yes
```

Windows 任务栏会出现一个浏览器窗口（WSLg）。用完直接关窗口，profile 会保留。

3. 无头自检（需要 xvfb：`sudo apt install -y xvfb`）：

```
ccs check profile1
```

## 容器模式

沙盒也能跑在容器里，用浏览器打开 noVNC 操作，不用 WSLg。

```
CCS_EXIT=socks5h://172.17.0.1:7897 \
  docker compose -f docker/compose.yaml up --build
```

然后打开 `http://localhost:6080/vnc.html`。

| 环境变量 | 作用 | 默认 |
|---|---|---|
| `CCS_NAME` | 沙盒名字，不存在就新建 | `sandbox` |
| `CCS_EXIT` | 新沙盒的出口 | 空（direct） |
| `CCS_SCREEN` | 虚拟屏幕，要和配置里的 `screen` 一致 | `2560x1440x24` |

* 容器里的 `127.0.0.1` 不是宿主机的代理。出口要写宿主机在容器网桥里的地址：
  Linux 用 `172.17.0.1`，Docker Desktop 用 `host.docker.internal`。
* 账号数据挂在宿主机的 `~/sandboxes`，容器重建不丢。
* 镜像基础是公开的 `python:3.13-slim`，依赖和引擎由 `./install.sh --auto` 装好。

## DNS：沙盒内保证干净

沙盒自带一个 DNS 中继（`src/ccs/network/dns_relay.py`）。`ccs web` 时在同一个进程里
起一个线程，关窗口或进程退出时一起停掉，不会留下子进程。

```
浏览器 ──域名──> 中继 :随机端口 ──DoH（经代理连 1.1.1.1）──> Cloudflare
                        └── IP ──> 上层代理（Clash）──> 出口
```

上层代理只收到 IP，看不到域名，所以**它的 DNS 配成什么样都不影响沙盒**。

| 点 | 做法 |
|---|---|
| 解析器 | 只用 Cloudflare DoH（连 `1.1.1.1` 的 IP，不查任何域名） |
| DoH 失败 | 直接失败，不退回系统 DNS |
| 缓存 | 按响应 TTL 缓存，最短 30 秒，最长 300 秒 |
| 上游 | 只支持 socks5；`http://` 出口跳过中继，由代理自己解析 |

已经验证过：把 Clash 的 DNS 段整个关掉（让它退回系统 DNS）后，沙盒里打开
ipleak 仍然只显示 Cloudflare 的解析器。

中继的实现只用 Python 标准库，不需要额外组件。日志写在沙盒目录的 `ccs.log`。

浏览器侧还关掉了所有绕过代理的通道（启动时写入 profile，可用 `profile/prefs.js` 核对）：

| 偏好 | 值 | 作用 |
|---|---|---|
| `network.proxy.socks_remote_dns` | `true` | 域名交给中继，浏览器自己不做 DNS |
| `network.dns.disablePrefetch` | `true` | 关掉提前解析 |
| `network.dns.disableIPv6` | `true` | 关掉 IPv6 解析 |
| `network.http.http3.enabled` | `false` | HTTP/3 走 UDP，SOCKS 不带 UDP |

说明：实测这个浏览器在配了代理时**不会**自己做 DoH（把 DoH 地址写坏，页面照样能开）。
所以 DNS 只能交给中继，`network.trr.*` 在这里不起作用。

验证：在沙盒窗口里打开 `ipleak.net`，DNS 那一栏应该只有 Cloudflare 的解析器，没有本机 ISP。

## 屏幕和字体

配置里可以固定这几样，其余指纹仍按种子生成：

```toml
exit = "socks5h://127.0.0.1:7897"
screen = "1920x1080"
dpr = 1
fonts = "western"
```

| 键 | 默认 | 含义 |
|---|---|---|
| `screen` | `1920x1080` | 屏幕面板尺寸（设备像素）。`auto` = 按种子生成 |
| `dpr` | `1` | 缩放比例。只能写 1、1.25、1.5、2（引擎只测过这四档） |
| `fonts` | `western` | 去掉简繁中文字体（雅黑、宋体等），保留日文和西文；`all` = 保留全部 |
| `strict_ip` | `true` | 出口 IP 和上次不一致时**拒绝启动**；`false` = 只提示不拦 |

页面读到的 `screen.width` 是面板尺寸除以缩放比例。默认这组：

```
面板 1920x1080，缩放 100%  →  页面看到 1920x1080
```

⚠️ **窗口的物理大小等于面板大小，和缩放无关。** 面板比显示器大，右边和下边就会被切掉。
所以 `screen` 不能超过自己显示器的分辨率。4K 面板在任何缩放下都是 3840 像素宽，
在 2200 宽的屏上放不下；想要 4K 的"高档感"但屏不够宽，只能接受窗口被切。

窗口大小由引擎固定成声明的尺寸，拖拽窗口不会让页面重新排版。

屏幕是成套的：可用高度比总高度少 48 像素（Windows 任务栏），窗口外框比屏幕宽 16 像素，
窗口位置是 `(-8, -8)`（最大化时边框在屏幕外）。现代码点深度固定 24 位。

字体只改清单，不动字体文件。页面探测 `"Microsoft YaHei"` 会落到兜底字体，
和一台没装中文语言包的 Windows 表现一致。

## 账号信息

沙盒登录 claude.ai 之后，`ccs` 会在页面里自动抓一次账号信息，存到
`~/sandboxes/<名字>/account.json`（权限 0600，里面有会话密钥）：

| 字段 | 来源 |
|---|---|
| `email` / `display_name` | `/api/account` |
| `account_created_at` | `/api/account`（UTC） |
| `org_id` / `org_name` / `org_created_at` | `/api/organizations` |
| `plan` | `/api/organizations` 的 `rate_limit_tier` |
| `session_key` | cookie `sessionKey` |

抓取时机：打开 claude.ai 之后一次，之后每 45 秒一次（中途登录也能记上）。

`ccs ls` 直接显示：

```
名字  出口 IP         账号                        档位  注册        组织
003   24.249.245.74   claude003@justlovecode.com  免费  2026-10-22  fe33070d-171d-4b8b-8202-df474cf81d05
```

颜色：名字加粗；档位按等级着色（Max 洋红、Pro 青色、免费压暗）；组织和缺失值压暗。
`ccs status` 会多出账号段：账号、档位、计费、组织、注册。

要 sessionKey 用 `ccs key 003`：直接复制到 Windows 剪贴板，只显示前 18 个字符。
加 `--show` 才完整打印。

⚠️ 抓取必须在浏览器里做。用 curl 带 cookie 请求会被 Cloudflare 拦掉，
返回 `Just a moment...`。

## 导出 / 导入身份

想把一个沙盒的指纹和登录态交给另一台机器：

```
ccs export 003 -o 003.bundle.tar.gz    # 约 10 KB
ccs import 003.bundle.tar.gz 003       # 对方机器上执行
ccs web 003
```

包里只有三样：

| 文件 | 内容 |
|---|---|
| `bundle.json` | 版本、出口、指纹 pin、账号摘要 |
| `seed.json` | 指纹种子 |
| `cookies.sqlite` | 只保留 claude.ai 的 cookie（含 `sessionKey`、`anthropic-device-id`） |

不带 profile 的其它内容（缓存、历史、storage），所以只有 10 KB 左右。

对方导入后：

- 指纹一致（种子 + pin 都带过去了）；
- 登录态一致（cookie 带过去了），打开就是登录状态；
- 出口由 `bundle.json` 记录，和原沙盒相同。

⚠️ 包里含 `sessionKey`，等于把这个号的登录权一起给出去了。共享后避免两端同时在线。

## 出口 IP 变了会怎样

严格模式默认开启。同一个沙盒第一次启动会记下出口 IP，以后每次启动都比对：

```
出口 IP : 24.249.245.74        ← 和记录一致，正常启动
出口 IP : 8.8.8.8
错误: 出口 IP 变了（上次 24.249.245.74，现在 8.8.8.8）。
      同一个号跨会话换 IP 是风险信号，已阻止启动。
```

确实要换（比如换了节点）时，两种办法：

```
ccs web 003 --allow-ip             # 这一次放行，并记下新 IP
```

或者把配置里的 `strict_ip` 改成 `false`，以后只提示不拦。

## 养号话术

默认从目录读，一个文件一句：

```
~/.config/claude-sandbox/prompts/
├── 01-greeting.md
├── 02-rewrite.md
└── 03-explain-cloud.md
```

```markdown
---
name: greeting
description: 早上的问候
---
你好，帮我写一句早上的问候语
```

`---` 头部是元数据，只用来描述；正文才是发出去的内容。按文件名排序，每次随机挑一句。

| 方式 | 配置 | 适合 |
|---|---|---|
| 目录（推荐） | `prompts_dir` | 增删改都只是加一个文件 |
| 单文件 | `prompts_file` | 一行一句、`---` 分段，或 `.csv`（第一列） |
| 数组 | `prompts` | 三五条短句 |
| 一次指定 | `ccs warmup 003 --prompt "…"` | 临时用 |

优先级：`--prompt` > `prompts_dir` > `prompts_file` > `prompts` > 内置默认。

## 本机 Clash 的注意点

本机 Clash 在**规则模式**下按域名分流。如果 Claude 的规则被指到别的节点（例如指到你自己的
VPS），出口就不是你以为的那个。要固定出口，请在 Clash 里给目标节点单独开一个监听端口，
再把那个端口写进 `exit`。

## 容器版（46.2 上跑，网页操作）

```
cd docker
docker compose up -d --build
```

容器里默认出口是 `socks5h://172.18.0.1:7871`（46.2 上的 Cox），可用 `SANDBOX_EXIT` 覆盖。
然后用 SSH 隧道访问，**不要把 6080 暴露到公网**：

```
ssh -L 6080:127.0.0.1:6080 root@64.83.46.2
```

浏览器打开：

```
http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=scale
```

这样只有"屏幕画面"走隧道，浏览器的网络流量不出 46.2。

## 出口 IP 会变

住宅节点换 IP 不受我们控制。沙盒第一次打开会记下出口 IP；以后变了会提示：

```
注意: 出口 IP 变了（上次 24.249.245.74，现在 162.225.43.129）
```

同一个号跨会话换 IP 是风险信号，看到提示就先别注册。

## 局限

- **沙盒不保证不被封。** 账号能不能活下来主要看注册物料（邮箱域名、地址样式、IP 信誉）
  和注册行为；沙盒只保证"不因为本机泄露被单独挑出来"。
- 平台没有"本计费周期"的接口；订阅日期只能看到首次订阅时间（`subscription_created_at`）。

## 开发

```
make test    # 单元测试（25 条）
make lint    # ruff：F/E/B/SIM/C90/RUF，圈复杂度上限 12
```

代码在 `src/ccs/`，分层：

| 模块 | 职责 |
|---|---|
| `cli.py` | 参数解析和分发，不含逻辑 |
| `config.py` | TOML 读取、默认值、校验、旧 JSON 迁移 |
| `paths.py` | 所有路径 |
| `errors.py` | `die()` / `say()` / `log_line()` |
| `sandbox/model.py` | `Sandbox` dataclass |
| `sandbox/store.py` | meta.json / account.json 读写（纯 IO） |
| `sandbox/lifecycle.py` | new / ls / status / mv / rm 等沙盒命令 |
| `network/proxy.py` | 出口 spec 解析 |
| `network/preflight.py` | 量出口 IP |
| `network/doh.py` | DoH 客户端（带 TTL 缓存） |
| `network/dns_relay.py` | 同进程 DNS 中继（线程） |
| `browser/prefs.py` | 从配置生成浏览器偏好 |
| `browser/process.py` | 进程查找、僵尸判断、清场 |
| `browser/launcher.py` | 起浏览器、pin、生命周期 |
| `browser/tasks.py` | open / warmup / harvest |
| `report.py` | ls / status / account 的终端表格 |

`ccs` 本身只剩启动器（27 行），负责切到仓库的 `.venv` 再跑 `python -m ccs`。
