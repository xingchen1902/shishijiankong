#!/bin/bash
set -e

echo "[启动] 补齐今日缺失事件..."
python3 -u /app/backfill.py

echo "[启动] 启动轮询监听（后台）..."
nohup python3 -u /app/main.py > /proc/1/fd/1 2>&1 &

echo "[启动] 启动 Web 看板..."
exec python3 -m uvicorn api:app --host 0.0.0.0 --port 8899 --log-level info
