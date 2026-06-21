#!/bin/bash
set -e

# 启动监控（后台）
python3 /app/main.py &

# 启动看板（前台，保持容器运行）
exec python3 -m uvicorn api:app --host 0.0.0.0 --port 8899 --log-level info
