#!/bin/bash
set -e

# 启动前先补齐今日缺失数据（从BJT 00:00到当前）
echo "[启动] 补齐今日数据..."
python3 -u /app/backfill.py

# 启动监控（后台）
nohup python3 -u /app/main.py > /tmp/monitor.log 2>&1 &

# 启动看板（前台，保持容器运行）
exec python3 -m uvicorn api:app --host 0.0.0.0 --port 8899 --log-level info
