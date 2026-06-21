# ARK 实时监控系统 - 部署文档

---

## 一、Docker 部署（推荐）

### 1. VPS 上安装 Docker

```bash
curl -fsSL https://get.docker.com | sh
apt install docker-compose-plugin -y
```

### 2. 克隆代码

```bash
cd /opt
git clone https://github.com/xingchen1902/shishijiankong.git bsc-monitor
cd bsc-monitor
```

### 3. 配置环境变量

```bash
cp .env.example .env
nano .env
```

**.env 文件内容：**

```
FEISHU_APP_ID=cli_xxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxx
TELEGRAM_BOT_TOKEN=123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11
TELEGRAM_CHAT_ID=-1001234567890
```

### 4. 启动

```bash
docker compose up -d --build
```

查看状态：

```bash
docker compose logs -f
```

浏览器打开 `http://VPS_IP:8899` 查看看板。

### 5. 管理命令

| 命令 | 说明 |
|---|---|
| `docker compose up -d --build` | 启动/重建 |
| `docker compose down` | 停止 |
| `docker compose restart` | 重启 |
| `docker compose logs -f` | 查看实时日志 |
| `docker compose pull` | 更新镜像 |

### 6. 更新代码后重新部署

```bash
cd /opt/bsc-monitor
git pull
docker compose up -d --build
```

---

## 二、自动部署（GitHub Actions）

推送代码到 GitHub 后自动部署到 VPS，无需手动 SSH。

### 1. GitHub 仓库配置 Secrets

在 GitHub 仓库 `Settings → Secrets and variables → Actions` 添加：

| Secret | 值 | 说明 |
|---|---|---|
| `VPS_HOST` | `34.4.105.101` | VPS IP |
| `VPS_USER` | `root` | SSH 用户 |
| `VPS_SSH_KEY` | `你的私钥内容` | SSH 私钥 |

### 2. 配置原理

- 每次推送 `main` 分支到 GitHub
- GitHub Actions 自动 SSH 到 VPS
- 执行 `git pull && docker compose up -d --build`

配置好后，以后只需推送代码到 GitHub，VPS 会自动拉取并重启。

---

## 三、手动部署（无 Docker，传统方式）

### 1. 上传到 VPS

```bash
scp -r /path/to/ark-dashboard/ root@34.4.105.101:/opt/ark-dashboard/
```

### 2. 安装依赖

```bash
cd /opt/ark-dashboard
pip install requests python-dotenv fastapi uvicorn
```

### 3. 运行

```bash
# 监控
python3 main.py &

# 看板
uvicorn api:app --host 0.0.0.0 --port 8899 &
```

---

## 四、注意事项

- SQLite 数据库: `/opt/bsc-monitor/data/ark_monitor.db`，Docker 下通过 volume 持久化
- 监控默认从最新区块开始，如需指定：`python3 main.py 12345678`
- 时区: 默认 `Asia/Shanghai` (BJT)
