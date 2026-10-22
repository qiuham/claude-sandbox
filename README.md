# claude-sandbox

一个 Claude 注册/养号用的沙盒。一个沙盒 = 一个独立 profile + 一个固定出口 + 一份固定指纹。
手工版给人在窗口里点，同一套脚本加 `--headless` 就能给自动化用。

## 命令

| 命令 | 作用 |
|---|---|
| `sandbox new <名字> [--exit 地址\|direct]` | 新建槽位 |
| `sandbox ls [--json]`（别名 `list`） | 列出槽位 |
| `sandbox status <名字> [--json]` | 槽位详情（出口、种子、profile 大小） |
| `sandbox start <名字>`（别名 `open`） | 启动窗口，手工注册 |
| `sandbox check <名字>` | 无头自检（出口 IP / WebRTC） |
| `sandbox set-exit <名字> <地址\|direct>` | 改槽位出口 |
| `sandbox rm <名字> [--force]` | 删除槽位（含 profile） |
| `sandbox doctor` | 检查环境和配置 |

## 设计

| 层 | 做法 |
|---|---|
| 网络 | 浏览器只连指定的代理地址，不依赖系统代理、不依赖 TUN。DNS 由代理侧解析 |
| 出口 | **不配 = direct（不用代理）；配了 = 走指定代理** |
| 浏览器 | invisible_playwright（改过的 Firefox）：指纹种子、时区/语言、WebRTC 都与出口一致 |
| 身份 | 每个沙盒独立 profile 目录；种子写在 `profile/seed.json`，以后一直用它 |

## 出口

只有两种：

| 写法 | 含义 |
|---|---|
| 不配置 / `--exit direct` | 完全不用代理，走原生环境（出口由本机的 TUN/透明代理决定） |
| 配置里写 `exit` / `--exit socks5h://…` | 显式走指定代理 |

配置文件 `~/.config/claude-sandbox/config.json`（见 `config.example.json`）：

```json
{
  "exit": "socks5h://127.0.0.1:7897"
}
```

* 文件不存在 = direct。
* `exit` 可以是任何 `socks5h://` / `http://` 地址。
* 命令行 `--exit <地址>` 或 `--exit direct` 优先于配置。

## 手工版（WSL / 本机）

1. 装依赖：

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m invisible_playwright fetch      # 下载浏览器引擎（约 260 MB）
```

2. 建槽位、启动、看状态：

```
./sandbox doctor
./sandbox new profile1                 # 有配置就走配置的出口，没有就 direct
./sandbox new profile2 --exit direct    # 显式不用代理
./sandbox ls                            # 列出所有槽位
./sandbox status profile1               # 出口、种子、profile 大小
./sandbox start profile1                # 启动窗口（open 是别名）
./sandbox set-exit profile1 socks5h://127.0.0.1:7897
./sandbox rm profile2                   # 删除前会要求输入 yes
```

Windows 任务栏会出现一个浏览器窗口（WSLg）。用完直接关窗口，profile 会保留。

3. 无头自检（需要 xvfb：`sudo apt install -y xvfb`）：

```
./sandbox check profile1
```

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
