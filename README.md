# claude-sandbox

一个 Claude 注册/养号用的沙盒。一个沙盒 = 一个独立 profile + 一个固定出口 + 一份固定指纹。
手工版给人在窗口里点，同一套脚本加 `--headless` 就能给自动化用。

## 设计

| 层 | 做法 |
|---|---|
| 网络 | 浏览器只连指定的代理地址，不依赖系统代理、不依赖 TUN。DNS 由代理侧解析 |
| 出口 | **可配置**：本机 Clash、46.2 上的 mihomo、任何 SOCKS/HTTP 代理都行 |
| 浏览器 | invisible_playwright（改过的 Firefox）：指纹种子、时区/语言、WebRTC 都与出口一致 |
| 身份 | 每个沙盒独立 profile 目录；种子写在 `profile/seed.json`，以后一直用它 |

## 出口：两种形态

出口是一个 spec，可以是配置里的名字，也可以直接写地址（`socks5h://host:port`）。

配置文件 `~/.config/claude-sandbox/config.json`（见 `config.example.json`）：

```json
{
  "default_exit": "local",
  "exits": {
    "local":  {"label": "本机 Clash", "proxy": "socks5h://127.0.0.1:7897"},
    "cox":    {"label": "46.2 的 Cox", "ssh": {"host": "64.83.46.2", "user": "root",
                                               "remote_port": 7871, "local_port": 7881}},
    "direct":  {"label": "原生环境", "direct": true},
    "cox-46.2": {"label": "在 46.2 上连 mihomo", "proxy": "socks5h://172.18.0.1:7871"}
  }
}
```

* `proxy` —— 显式选一个代理。本机 Clash、46.2 上的 mihomo、第三方代理都用这种。
* `direct` —— **完全不用代理**，浏览器走原生环境。出口是多少取决于这台机器自己有没有
  TUN / 透明代理；沙盒不替你设。
* `ssh` —— 出口只在另一台机器上能连时，用 SSH 本地转发拉过来。

不写配置也能用，内置默认：`local`（7897）、`direct`（不用代理）、
`cox`/`wave`/`att`（经 46.2 的 SSH 隧道）、`cox-46.2`/`wave-46.2`/`att-46.2`（连 46.2 的 mihomo）。

### 本机 Clash 的注意点

本机 Clash 在**规则模式**下按域名分流。如果 Claude 的规则被指到别的节点（例如指到你自己的
VPS），出口就不是你以为的那个。要固定出口，请在 Clash 里给目标节点单独开一个监听端口，
再把那个端口写进配置。

## 手工版（WSL / 本机）

1. 装依赖：

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m invisible_playwright fetch      # 下载浏览器引擎（约 260 MB）
```

2. 如果用 `ssh` 形态的出口，准备密码文件（46.2 关掉了密钥登录）：

```
mkdir -p ~/.config/claude-sandbox
echo '服务器的 root 密码' > ~/.config/claude-sandbox/ssh-password
chmod 600 ~/.config/claude-sandbox/ssh-password
```

3. 建沙盒、开窗口：

```
./sandbox doctor                 # 看出口列表和依赖
./sandbox new demo --exit local  # 或者 --exit cox / --exit socks5h://1.2.3.4:1080
./sandbox open demo
```

Windows 任务栏会出现一个浏览器窗口（WSLg）。用完直接关窗口，profile 会保留。

4. 无头自检（需要 xvfb：`sudo apt install -y xvfb`）：

```
./sandbox check demo
```

## 容器版（46.2 上跑，网页操作）

```
cd docker
docker compose up -d --build
```

容器里默认用 `cox-46.2`（连 46.2 的 mihomo）。然后用 SSH 隧道访问，
**不要把 6080 暴露到公网**：

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
