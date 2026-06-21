#!/usr/bin/env python3
"""
FastAPI 看板 API + 静态页面托管
"""

import os
from datetime import datetime, timezone, timedelta
try:
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import FileResponse
except ImportError:
    FastAPI = None

from db import get_all_daily, get_conn, get_daily_summary
from event_parser import get_balance, BONUS_POOL, STAKE_POOL, TOKEN_ARK, DECIMALS
from pusher import push_to_feishu, push_to_telegram

BJT = timezone(timedelta(hours=8))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

app = None
if FastAPI:
    app = FastAPI(title="ARK 实时监控")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    @app.get("/")
    def index():
        return FileResponse(os.path.join(BASE_DIR, "dashboard.html"))

    @app.get("/api/daily")
    def get_daily():
        data = get_all_daily()
        return {"data": data, "count": len(data)}

    @app.get("/api/latest")
    def get_latest():
        data = get_all_daily()
        return {"data": data[0] if data else None}

    @app.get("/api/today")
    def get_today():
        """当日实时汇总"""
        today = datetime.now(BJT).strftime("%Y-%m-%d")
        conn = get_conn()
        row = conn.execute("""
            SELECT
                COALESCE(SUM(CASE WHEN type='bonus_withdraw' THEN value ELSE 0 END),0) as bonus_out,
                COALESCE(SUM(CASE WHEN type='stake_in' THEN value ELSE 0 END),0) as stake_in,
                COALESCE(SUM(CASE WHEN type='stake_out' THEN value ELSE 0 END),0) as stake_out,
                COALESCE(SUM(CASE WHEN type='static_burn' THEN value ELSE 0 END),0) as static_burn,
                COALESCE(SUM(CASE WHEN type='dynamic' THEN value ELSE 0 END),0) as dynamic_in,
                COUNT(*) as event_count,
                MAX(block) as last_block
            FROM events
            WHERE (created_at >= datetime('now', '-1 day') OR (timestamp IS NOT NULL AND timestamp LIKE ?))
        """, (today + "%")).fetchone()
        conn.close()

        bonus_bal = get_balance(TOKEN_ARK, BONUS_POOL) / 10**DECIMALS
        stake_bal = get_balance(TOKEN_ARK, STAKE_POOL) / 10**DECIMALS

        bonus_out = float(row["bonus_out"]) if row["bonus_out"] else 0
        stake_in_val = float(row["stake_in"]) if row["stake_in"] else 0
        stake_out = float(row["stake_out"]) if row["stake_out"] else 0
        static_burn = float(row["static_burn"]) if row["static_burn"] else 0
        dynamic_in = float(row["dynamic_in"]) if row["dynamic_in"] else 0
        event_count = int(row["event_count"]) if row["event_count"] else 0
        last_block = int(row["last_block"]) if row["last_block"] else 0
        net_stake = stake_in_val - stake_out - bonus_out

        return {
            "data": {
                "date": today,
                "bonus_balance": round(bonus_bal, 2),
                "bonus_withdraw": round(bonus_out, 2),
                "static_burn": round(static_burn, 2),
                "dynamic_in": round(dynamic_in, 2),
                "stake_balance": round(stake_bal, 2),
                "stake_in": round(stake_in_val, 2),
                "stake_out": round(stake_out, 2),
                "net_stake": round(net_stake, 2),
                "event_count": event_count,
                "last_block": last_block,
            }
        }

    @app.get("/api/balances")
    def get_balances():
        bonus_bal = get_balance(TOKEN_ARK, BONUS_POOL) / 10**DECIMALS
        stake_bal = get_balance(TOKEN_ARK, STAKE_POOL) / 10**DECIMALS
        return {
            "bonus_balance": round(bonus_bal, 2),
            "stake_balance": round(stake_bal, 2),
        }

    @app.get("/api/realtime")
    def get_realtime(limit: int = 100):
        conn = get_conn()
        rows = conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        conn.close()
        return {"data": [dict(r) for r in rows]}

    @app.get("/api/force-summary")
    def force_summary():
        today = datetime.now(BJT).strftime("%Y-%m-%d")
        conn = get_conn()
        row = conn.execute("""
            SELECT
                COALESCE(SUM(CASE WHEN type='bonus_withdraw' THEN value ELSE 0 END),0) as bonus_out,
                COALESCE(SUM(CASE WHEN type='stake_in' THEN value ELSE 0 END),0) as stake_in,
                COALESCE(SUM(CASE WHEN type='stake_out' THEN value ELSE 0 END),0) as stake_out,
                COALESCE(SUM(CASE WHEN type='static_burn' THEN value ELSE 0 END),0) as static_burn,
                COALESCE(SUM(CASE WHEN type='dynamic' THEN value ELSE 0 END),0) as dynamic_in
            FROM events
            WHERE (created_at >= datetime('now', '-1 day') OR (timestamp IS NOT NULL AND timestamp LIKE ?))
        """, (today + "%")).fetchone()
        conn.close()

        bonus_bal = get_balance(TOKEN_ARK, BONUS_POOL) / 10**DECIMALS
        stake_bal = get_balance(TOKEN_ARK, STAKE_POOL) / 10**DECIMALS

        bonus_out = float(row["bonus_out"]) if row["bonus_out"] else 0
        stake_in_val = float(row["stake_in"]) if row["stake_in"] else 0
        stake_out = float(row["stake_out"]) if row["stake_out"] else 0
        static_burn = float(row["static_burn"]) if row["static_burn"] else 0
        dynamic_in = float(row["dynamic_in"]) if row["dynamic_in"] else 0
        net_stake = stake_in_val - stake_out - bonus_out

        record = {
            "date": today,
            "bonus_balance": round(bonus_bal, 2),
            "bonus_withdraw": round(bonus_out, 2),
            "static_burn": round(static_burn, 2),
            "dynamic_in": round(dynamic_in, 2),
            "stake_balance": round(stake_bal, 2),
            "stake_in": round(stake_in_val, 2),
            "stake_out": round(stake_out, 2),
            "net_stake": round(net_stake, 2),
        }
        push_to_feishu(record)
        push_to_telegram(record)
        return {"status": "ok", "data": record}

    @app.get("/health")
    def health():
        return {"status": "ok", "time": datetime.now(BJT).isoformat()}
