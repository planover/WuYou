# WuYou（一坞邮）部署指南

> 面向零基础用户，跟着图文一步步来，10 分钟搭好你自己的邮箱中心。

---

## 准备工作

- 一台安装了 **Docker** 和 **Docker Compose** 的电脑 / NAS / 服务器。
- 如果还没装 Docker，请先看官方教程：
  - [Docker Desktop（Windows / macOS）](https://docs.docker.com/get-docker/)
  - [Docker Engine（Linux 服务器）](https://docs.docker.com/engine/install/)

验证 Docker 是否安装成功（终端 / PowerShell 中运行）：

```bash
docker --version
docker compose version
```

两条命令都正常输出版本号即可。

---

## Step 1：创建目录

打开终端（macOS/Linux）或 PowerShell（Windows），执行：

```bash
mkdir wuyou
cd wuyou
```

---

## Step 2：下载 docker-compose.yml

```bash
curl -O https://raw.githubusercontent.com/your-username/WuYou/main/docker-compose.yml
```

> 如果 `curl` 不可用（部分 Windows 环境），可以直接用浏览器打开上方链接，右键"另存为"到 `wuyou` 文件夹。

---

## Step 3：启动（一行命令）

```bash
docker compose up -d
```

首次运行会自动拉取镜像（约 2-5 分钟，取决于网速），之后都是秒启动。

看到类似输出说明成功：

```
[+] Running 1/1
 ✔ Container wuyou  Started
```

---

## Step 4：打开浏览器

在浏览器地址栏输入：

```
http://localhost:8000
```

如果部署在 NAS / 远程服务器上，把 `localhost` 换成那台机器的 IP 地址，例如：

```
http://192.168.1.100:8000
```

---

## 进阶操作

### 开机自启

```bash
docker compose up -d --restart unless-stopped
```

或在 `docker-compose.yml` 中为服务添加：

```yaml
restart: unless-stopped
```

### 查看运行状态

```bash
docker compose ps
```

---

## 常见问题排错

### 1. 端口 8000 被占用怎么办？

**现象：** 启动时报错 `bind: address already in use` 或 `port is already allocated`。

**解决：**

先查是谁占用了 8000 端口：

```bash
# Windows（PowerShell）
netstat -ano | findstr :8000

# macOS / Linux
lsof -i :8000
```

然后有两个选择：

- **方案 A：关掉占用程序。** 记下 PID，在任务管理器或 `kill` 中结束它。
- **方案 B：改端口。** 打开 `docker-compose.yml`，把 `ports` 里的 `8000:8000` 改成 `9000:8000`（左边是宿主机端口，你可以自定义），然后访问 `http://localhost:9000`。

---

### 2. 数据库被锁怎么修复？

**现象：** 页面提示 `database is locked` 或 SQLite 相关错误。

**原因：** 通常是异常断电、强制关机或同时运行了多个实例导致。

**解决步骤：**

```bash
# 1. 停掉容器
docker compose down

# 2. 进入数据目录（默认在 wuyou/data 下）
# 3. 备份当前数据库（好习惯）
cp data/wuyou.db data/wuyou.db.bak

# 4. 尝试修复（需要本机装了 sqlite3）
sqlite3 data/wuyou.db "PRAGMA integrity_check;"

# 5. 如果修复成功，重启容器
docker compose up -d
```

如果没有装 `sqlite3`，可以先安装（`apt install sqlite3` / `brew install sqlite3`），或者直接用备份文件替换后重启。

---

### 3. 权限问题（Linux）

**现象：** 容器启动后页面打不开，日志里有 `Permission denied`。

**原因：** Docker 容器内的用户（通常是 root 或无权限用户）无法读写宿主机挂载的数据目录。

**解决：**

```bash
# 给数据目录开放权限
sudo chown -R 1000:1000 ./data
sudo chmod -R 755 ./data
```

也可以在 `docker-compose.yml` 中显式指定 `user`：

```yaml
services:
  wuyou:
    user: "1000:1000"
```

---

### 4. Docker 容器起不来怎么查日志？

**现象：** `docker compose up -d` 执行后，浏览器访问没反应。

**排错流程：**

```bash
# 查看容器是否在运行
docker compose ps

# 如果状态是 Restarting 或 Exited，查看日志
docker compose logs wuyou

# 想看最近 50 行日志
docker compose logs --tail 50 wuyou

# 实时跟踪日志（Ctrl+C 退出）
docker compose logs -f wuyou
```

常见日志关键词：

| 日志关键词 | 可能原因 |
|---|---|
| `ModuleNotFoundError` | 镜像不完整，尝试 `docker compose build --no-cache` |
| `Connection refused` | 依赖服务（如外部 API）不可达，检查网络 |
| `Permission denied` | 参考上一节权限问题 |
| `disk I/O error` | 磁盘空间不足，`df -h` 查看 |

---

### 5. 如何升级到最新版？

**步骤：**

```bash
# 1. 进入 wuyou 目录
cd wuyou

# 2. 拉取最新的 docker-compose.yml（如果有更新）
curl -O https://raw.githubusercontent.com/your-username/WuYou/main/docker-compose.yml

# 3. 拉取最新镜像并重建容器
docker compose pull
docker compose up -d

# 4. 清理旧镜像（释放磁盘空间）
docker image prune -f
```

> WuYou 支持热更新。小版本更新时，拉取新镜像重建容器即可，**无需手动迁移数据**。数据目录（`./data`）与容器分离，升级不会丢失任何数据。

---

## 更多帮助

- [项目首页](https://github.com/your-username/WuYou)
- [更新日志](../CHANGELOG.md)
- [提交 Issue](https://github.com/your-username/WuYou/issues)
