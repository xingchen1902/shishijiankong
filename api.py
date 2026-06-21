#!/usr/bin/env python3
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
import os
from datetime import datetime, timezone, timedelta
from db import get_all_daily, get_conn
from event_parser import get_balance, BONUS_POOL, STAKE_POOL, TOKEN_ARK, DECIMALS
from pusher import push_to_feishu, push_to_telegram

BJT = timezone(timedelta(hours=8))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = FastAPI(title="ARK")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def get_today_data():
    today = datetime.now(BJT).strftime("%Y-%m-%d")
    conn = get_conn()
    row = conn.execute("""
        SELECT COALESCE(SUM(CASE WHEN type='bonus_withdraw' THEN value ELSE 0 END),0),
               COALESCE(SUM(CASE WHEN type='stake_in' THEN value ELSE 0 END),0),
               COALESCE(SUM(CASE WHEN type='stake_out' THEN value ELSE 0 END),0),
               COALESCE(SUM(CASE WHEN type='static_burn' THEN value ELSE 0 END),0),
               COALESCE(SUM(CASE WHEN type='dynamic' THEN value ELSE 0 END),0),
               COUNT(*), MAX(block)
        FROM events WHERE timestamp LIKE ?
    """, (today + "%",)).fetchone()
    conn.close()
    bonus_bal = get_balance(TOKEN_ARK, BONUS_POOL) / 10**DECIMALS
    stake_bal = get_balance(TOKEN_ARK, STAKE_POOL) / 10**DECIMALS
    bo = float(row[0]) if row[0] else 0
    si = float(row[1]) if row[1] else 0
    so = float(row[2]) if row[2] else 0
    sb = float(row[3]) if row[3] else 0
    di = float(row[4]) if row[4] else 0
    ec = int(row[5]) if row[5] else 0
    lb = int(row[6]) if row[6] else 0
    return {"date":today,"bonus_balance":round(bonus_bal,2),"bonus_withdraw":round(bo,2),
            "static_burn":round(sb,2),"dynamic_in":round(di,2),
            "dynamic_turbo":round(max(di-sb,0),2),"stake_balance":round(stake_bal,2),
            "stake_in":round(si,2),"stake_out":round(so,2),"net_stake":round(si-so-bo,2),
            "event_count":ec,"last_block":lb}

@app.get("/api/today")
def get_today():
    return {"data": get_today_data()}

@app.get("/api/daily")
def get_daily():
    return {"data": get_all_daily(), "count": 0}

@app.get("/api/balances")
def get_balances():
    return {"bonus_balance":round(get_balance(TOKEN_ARK,BONUS_POOL)/10**DECIMALS,2),
            "stake_balance":round(get_balance(TOKEN_ARK,STAKE_POOL)/10**DECIMALS,2)}

@app.get("/api/realtime")
def get_realtime(limit:int=100):
    conn = get_conn()
    rows = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?",(limit,)).fetchall()
    conn.close()
    return {"data": [dict(r) for r in rows]}

@app.get("/api/force-summary")
def force_summary():
    record = get_today_data()
    push_to_feishu(record)
    push_to_telegram(record)
    return {"status":"ok","data":record}

@app.get("/health")
def health():
    return {"status":"ok","time":datetime.now(BJT).isoformat()}

@app.get("/")
def index():
    return FileResponse(os.path.join(BASE_DIR, "dashboard.html"))
