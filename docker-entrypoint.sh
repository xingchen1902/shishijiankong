#!/bin/bash
set -e

# 启动监控（后台）- 用 nohup 确保不会被 kill
nohup python3 -u /app/main.py > /tmp/monitor.log 2>&1 &

# 启动看板（前台，保持容器运行）
exec python3 -m uvicorn api:app --host 0.0.0.0 --port 8899 --log-level info
