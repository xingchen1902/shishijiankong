# ARK 实时监控系统 - VPS 部署文档

## 环境要求

- Linux (Ubuntu 20.04+ / CentOS 7+)
- Python 3.9+
- systemd（推荐）或 screen

---

## 一、部署步骤

### 1. 上传到 VPS

在本地执行：



### 2. VPS 上安装依赖

Collecting requests
  Using cached requests-2.34.2-py3-none-any.whl.metadata (4.8 kB)
Collecting python-dotenv
  Using cached python_dotenv-1.2.2-py3-none-any.whl.metadata (27 kB)
Collecting charset_normalizer<4,>=2 (from requests)
  Using cached charset_normalizer-3.4.7-cp314-cp314-macosx_10_15_universal2.whl.metadata (40 kB)
Collecting idna<4,>=2.5 (from requests)
  Using cached idna-3.18-py3-none-any.whl.metadata (6.1 kB)
Collecting urllib3<3,>=1.26 (from requests)
  Using cached urllib3-2.7.0-py3-none-any.whl.metadata (6.9 kB)
Collecting certifi>=2023.5.7 (from requests)
  Using cached certifi-2026.6.17-py3-none-any.whl.metadata (2.5 kB)
Using cached requests-2.34.2-py3-none-any.whl (73 kB)
Using cached charset_normalizer-3.4.7-cp314-cp314-macosx_10_15_universal2.whl (309 kB)
Using cached idna-3.18-py3-none-any.whl (65 kB)
Using cached urllib3-2.7.0-py3-none-any.whl (131 kB)
Using cached python_dotenv-1.2.2-py3-none-any.whl (22 kB)
Using cached certifi-2026.6.17-py3-none-any.whl (133 kB)
Installing collected packages: urllib3, python-dotenv, idna, charset_normalizer, certifi, requests

Successfully installed certifi-2026.6.17 charset_normalizer-3.4.7 idna-3.18 python-dotenv-1.2.2 requests-2.34.2 urllib3-2.7.0

### 3. 配置环境变量

Incomplete terminfo entry

**.env 文件内容示例：**



### 4. 快速测试



### 5. 配置 systemd 自启服务（推荐）



### 6. 可选：启动看板 API



---

## 二、常用命令

| 命令 | 说明 |
|---|---|
|  | 启动 |
|  | 停止 |
|  | 重启 |
|  | 查看状态 |
|  | 查看实时日志 |
|  | 查看日志文件 |

---

## 三、无 systemd 时使用 screen

Must be connected to a terminal.
Must be connected to a terminal.
No Sockets found in /var/folders/fl/bthvvg_j6qg84z1wnngg55xr0000gn/T/.screen.


---

## 四、注意事项

### 数据文件位置
- SQLite 数据库: 
- 数据自动持久化，重启不丢失

### 首次启动
- 默认从当前最新区块开始监听
- 如需从指定区块开始：

### 日志
- 默认输出到 stdout（systemd 会捕获到 journald）
- 无日志轮转，建议自行配置 logrotate 如需文件日志

### 资源占用
- CPU: 极低（每秒 1 次 HTTP 请求）
- 内存: ~50MB
- 磁盘: 每天约 100KB（SQLite）

### 故障恢复
- 如果进程挂了，systemd 会自动重启（Restart=always）
- 断线后会自动重连 RPC
- 如果长时间断连，启动时会从上次最后一个已处理的区块继续
