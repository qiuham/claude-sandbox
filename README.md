# claude-sandbox

一个 Claude 注册/养号用的沙盒。一个沙盒 = 一个独立 profile + 一个固定出口 + 一份固定指纹。
手工版给人在窗口里点，同一套脚本加 `--headless` 就能给自动化用。

## 设计

| 层 | 做法 |
|---|---|
| 网络 | 浏览器只连指定 SOCKS 端口，不依赖系统代理、不依赖 TUN。DNS 由代理侧解析 |
| 出口 | 46.2 上 mihomo 的监听端口（一个端口一个住宅节点），本机用 SSH 隧道拉过来 |
| 浏览器 | invisible_playwright（改过的 Firefox）：指纹种子、时区/语言、WebRTC 都与出口一致 |
| 身份 | 每个沙盒独立 profile 目录；种子写在 `profile/seed.json`，以后一直用这一份 |

## 手工版（WSL）

1. 装依赖：

```
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m invisible_playwright fetch      # 下载浏览器引擎（约 260 MB）
```

2. 准备 SSH 密码文件（46.2 关掉了密钥登录）：

```
mkdir -p ~/.config/claude-sandbox
echo '服务器的 root 密码' > ~/.config/claude-sandbox/ssh-password
chmod 600 ~/.config/claude-sandbox/ssh-password
```

3. 建沙盒、开窗口：

```
./sandbox doctor
./sandbox new demo --exit cox
./sandbox open demo
```

Windows 任务栏会出现一个浏览器窗口（WSLg）。用完直接关窗口，profile 会保留。

4. 无头自检（不进窗口也能看出口和 WebRTC）：

```
./sandbox check demo
```

## 容器版（46.2 上跑，网页操作）

```
cd docker
docker compose up -d --build
```

然后用 SSH 隧道访问（不要把 6080 暴露到公网）：

```
ssh -L 6080:127.0.0.1:6080 root@64.83.46.2
```

浏览器打开：

```
http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=scale
```

容器里 `SANDBOX_DIRECT=1`，直接连 `172.18.0.1:<出口端口>`，不需要隧道再套隧道。

## 出口

| 名字 | 节点 | 46.2 端口 | 本机隧道端口 |
|---|---|---|---|
| `cox` | 美国 Cox 家宽 | 7871 | 7881 |
| `wave` | 美国 Wave 家宽 | 7872 | 7882 |
| `att` | 美国 AT&T 家宽 | 7873 | 7883 |

日本那条（7874）是机房 IP，已停用。

## 局限

- **沙盒不保证不被封。** 账号能不能活下来主要看注册物料（邮箱域名、地址样式、IP 信誉）
  和注册行为；沙盒只保证"不因为本机泄露被单独挑出来"。
- 平台没有"本计费周期"的接口；订阅日期只能看到首次订阅时间（`subscription_created_at`）。
