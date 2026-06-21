#!/usr/bin/env python3
"""
FastAPI 看板 API
- /api/daily - 历史汇总数据
- /api/latest - 最新数据
- /api/realtime - 最新事件
"""

import json
from datetime import datetime, timezone, timedelta
try:
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
except ImportError:
    FastAPI = None

from db import get_all_daily, get_conn

BJT = timezone(timedelta(hours=8))

app = None
if FastAPI:
    app = FastAPI(title="ARK 实时监控")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.get("/api/daily")
    def get_daily():
        data = get_all_daily()
        return {"data": data, "count": len(data)}

    @app.get("/api/latest")
    def get_latest():
        data = get_all_daily()
        return {"data": data[0] if data else None}

    @app.get("/api/realtime")
    def get_realtime(limit: int = 50):
        conn = get_conn()
        rows = conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return {"data": [dict(r) for r in rows]}

    @app.get("/health")
    def health():
        return {"status": "ok", "time": datetime.now(BJT).isoformat()}
