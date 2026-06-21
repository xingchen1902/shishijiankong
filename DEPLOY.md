# ARK 实时监控系统 - VPS 部署文档

## 环境要求

- Linux (Ubuntu 20.04+ / CentOS 7+)
- Python 3.9+

---

## 一、部署步骤

### 1. 上传到 VPS



### 2. VPS 上安装依赖

Requirement already satisfied: requests in ./venv/lib/python3.14/site-packages (2.34.2)
Requirement already satisfied: python-dotenv in ./venv/lib/python3.14/site-packages (1.2.2)
Collecting fastapi
  Downloading fastapi-0.138.0-py3-none-any.whl.metadata (27 kB)
Collecting uvicorn
  Downloading uvicorn-0.49.0-py3-none-any.whl.metadata (6.7 kB)
Requirement already satisfied: charset_normalizer<4,>=2 in ./venv/lib/python3.14/site-packages (from requests) (3.4.7)
Requirement already satisfied: idna<4,>=2.5 in ./venv/lib/python3.14/site-packages (from requests) (3.18)
Requirement already satisfied: urllib3<3,>=1.26 in ./venv/lib/python3.14/site-packages (from requests) (2.7.0)
Requirement already satisfied: certifi>=2023.5.7 in ./venv/lib/python3.14/site-packages (from requests) (2026.6.17)
Collecting starlette>=0.46.0 (from fastapi)
  Downloading starlette-1.3.1-py3-none-any.whl.metadata (6.4 kB)
Collecting pydantic>=2.9.0 (from fastapi)
  Using cached pydantic-2.13.4-py3-none-any.whl.metadata (109 kB)
Collecting typing-extensions>=4.8.0 (from fastapi)
  Using cached typing_extensions-4.15.0-py3-none-any.whl.metadata (3.3 kB)
Collecting typing-inspection>=0.4.2 (from fastapi)
  Using cached typing_inspection-0.4.2-py3-none-any.whl.metadata (2.6 kB)
Collecting annotated-doc>=0.0.2 (from fastapi)
  Downloading annotated_doc-0.0.4-py3-none-any.whl.metadata (6.6 kB)
Collecting click>=7.0 (from uvicorn)
  Downloading click-8.4.1-py3-none-any.whl.metadata (2.6 kB)
Collecting h11>=0.8 (from uvicorn)
  Downloading h11-0.16.0-py3-none-any.whl.metadata (8.3 kB)
Collecting annotated-types>=0.6.0 (from pydantic>=2.9.0->fastapi)
  Using cached annotated_types-0.7.0-py3-none-any.whl.metadata (15 kB)
Collecting pydantic-core==2.46.4 (from pydantic>=2.9.0->fastapi)
  Using cached pydantic_core-2.46.4-cp314-cp314-macosx_11_0_arm64.whl.metadata (6.6 kB)
Collecting anyio<5,>=3.6.2 (from starlette>=0.46.0->fastapi)
  Downloading anyio-4.14.0-py3-none-any.whl.metadata (4.6 kB)
Downloading fastapi-0.138.0-py3-none-any.whl (126 kB)
Downloading uvicorn-0.49.0-py3-none-any.whl (71 kB)
Downloading annotated_doc-0.0.4-py3-none-any.whl (5.3 kB)
Downloading click-8.4.1-py3-none-any.whl (116 kB)
Downloading h11-0.16.0-py3-none-any.whl (37 kB)
Using cached pydantic-2.13.4-py3-none-any.whl (472 kB)
Using cached pydantic_core-2.46.4-cp314-cp314-macosx_11_0_arm64.whl (2.0 MB)
Using cached annotated_types-0.7.0-py3-none-any.whl (13 kB)
Downloading starlette-1.3.1-py3-none-any.whl (73 kB)
Downloading anyio-4.14.0-py3-none-any.whl (123 kB)
Using cached typing_extensions-4.15.0-py3-none-any.whl (44 kB)
Using cached typing_inspection-0.4.2-py3-none-any.whl (14 kB)
Installing collected packages: typing-extensions, h11, click, anyio, annotated-types, annotated-doc, uvicorn, typing-inspection, starlette, pydantic-core, pydantic, fastapi

Successfully installed annotated-doc-0.0.4 annotated-types-0.7.0 anyio-4.14.0 click-8.4.1 fastapi-0.138.0 h11-0.16.0 pydantic-2.13.4 pydantic-core-2.46.4 starlette-1.3.1 typing-extensions-4.15.0 typing-inspection-0.4.2 uvicorn-0.49.0

### 3. 配置环境变量

Incomplete terminfo entry

**.env 文件内容示例：**



### 4. 快速测试



### 5. 配置 systemd 服务



---

## 二、常用命令

| 命令 | 说明 |
|---|---|
|  | 监控服务控制 |
|  | 看板服务控制 |
|  | 查看监控状态 |
|  | 监控实时日志 |
|  | 看板实时日志 |
|  | 测试看板 |

---

## 三、无 systemd 时使用 screen

Must be connected to a terminal.
Must be connected to a terminal.
Must be connected to a terminal.
Must be connected to a terminal.

---

## 四、看板界面

浏览器打开 ，包含：

- **4 个实时指标卡**：奖金池余额、质押池余额、今日事件数、最新区块
- **当日实时汇总**：奖金池提取、新增质押、赎回、净质押、静态/动静态涡轮
- **历史数据表**：近 30 天每日汇总
- **实时事件流**：最新 50 条链上事件
- **自动刷新**：每 30 秒刷新一次

---

## 五、注意事项

### 数据文件位置
- SQLite 数据库: 
- 数据自动持久化，重启不丢失

### 首次启动
- 默认从当前最新区块开始监听
- 如需从指定区块开始：

### 日志
- 监控输出到 systemd journal（）
- 看板输出到 systemd journal（）

### 资源占用
- CPU: 极低（每秒 1 次 HTTP 请求）
- 内存: ~50MB
- 磁盘: 每天约 100KB（SQLite）

### 故障恢复
- systemd 自动重启（Restart=always）
- 断线自动重连 RPC
- 重启后从上次最后处理的区块继续
