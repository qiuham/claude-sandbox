# ccs-cloud

账号包同步服务。单文件、只用标准库。

## 为什么这样设计

- **按邮箱存**，不按本地名字。两个人可能都有个 006，邮箱不会重复。
- **上传不覆盖**。每次上传都是一个新版本，要声明基于哪一版；不是最新就拒绝。
- **旧版本都留着**。推错了能回退，也能看到谁在什么时候改过。
- **包里有 sessionKey**，等于登录权。所以只监听本机端口，外面必须走 HTTPS。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/me` | 我是谁 |
| GET | `/v1/accounts` | 账号列表（最新版） |
| GET | `/v1/accounts/<邮箱>` | 下载最新版；加 `?version=5` 下载第 5 版 |
| GET | `/v1/accounts/<邮箱>/versions` | 版本历史 |
| GET | `/v1/accounts/<邮箱>/find?sha256=…` | 这份内容是哪一版 |
| POST | `/v1/accounts/<邮箱>/versions?base=5&message=…&force=0` | 上传 |
| DELETE | `/v1/accounts/<邮箱>` | 删（只有上传者或管理员） |
| POST | `/v1/users` | 建人（只有管理员），返回一次 token |

鉴权：`Authorization: Bearer <token>`。

上传规则：

| `base` | 服务端现在 | 结果 |
|---|---|---|
| 0 | 没有这个号 | 建 v1 |
| 0 | 有，且内容在历史里 | 直接回那一版，不新增 |
| 0 | 有，内容是新的 | 409，除非 `force=1` |
| 等于最新版 | | 新版本 |
| 不是最新版 | | 409，回最新版号 |

## 部署

```bash
# 在 46.2 上，仓库的 server/ 目录
CCS_CLOUD_ADMIN_TOKEN=<随机串> docker compose up -d --build
```

只在 127.0.0.1:8787 监听。前面用同机的反向代理加域名和证书。

首次启动用 `CCS_CLOUD_ADMIN_TOKEN` 建管理员；之后用管理员的 token
调 `POST /v1/users` 建人（客户端是 `ccs cloud user add <名字>`）。

## 数据

```
/data/cloud.db           用户、账号、版本索引
/data/blobs/<sha>.tar.gz 包内容，按内容哈希去重
```
