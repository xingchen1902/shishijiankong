#!/usr/bin/env python3
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import os, threading, time, requests
from datetime import datetime, timezone, timedelta
from db import get_all_daily_until_yesterday, get_conn
from event_parser import get_balance, BONUS_POOL, STAKE_POOL, TOKEN_ARK, DECIMALS
from pusher import push_to_feishu, push_to_telegram, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

BJT = timezone(timedelta(hours=8))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = FastAPI(title="ARK")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
import os.path
logo_path = os.path.join(BASE_DIR, "logo.png")
if os.path.exists(logo_path):
    @app.get("/logo.png")
    def get_logo():
        return FileResponse(logo_path, media_type="image/png")

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
            "stake_in":round(si,2),"stake_out":round(so,2),"net_stake":round(si-so,2),
            "event_count":ec,"last_block":lb}

# ---------- Telegram /today 命令轮询 ----------

def _send_today(record):
    """只推 Telegram，不写飞书"""
    from pusher import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    import requests
    def f(n): return f"{float(n):,.2f}"
    msg = f"""<b>📊 ARK 链上数据 · {record['date']}</b>

━━━━━━━━━━━━━━━━━━━━━

<b>💰 奖金池</b>
余额：{f(record['bonus_balance'])} ARK
当日提取：<code>{f(record['bonus_withdraw'])}</code> ARK

<b>🔒 质押池</b>
余额：{f(record['stake_balance'])} ARK
新增质押：<code>{f(record['stake_in'])}</code> ARK
赎回：<code>{f(record['stake_out'])}</code> ARK
净质押：<b>{f(record['net_stake'])}</b> ARK

<b>⚡ 涡轮</b>
静态涡轮：{f(record.get('static_burn',0))} ARK
动态涡轮：{f(max(record.get('dynamic_in',0)-record.get('static_burn',0),0))} ARK
动静态涡轮：{f(record.get('dynamic_in',0))} ARK

━━━━━━━━━━━━━━━━━━━━━
<i>实时监控 · 每日汇总</i>"""
    reply_markup = {"inline_keyboard": [[{"text": "📊 打开看板", "url": "http://arkcy.duckdns.org/"}]]}
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML",
              "disable_web_page_preview": True, "reply_markup": reply_markup}, timeout=15)


def _send_today(record):
    """只推 Telegram，不写飞书"""
    from pusher import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    import requests
    def f(n): return f"{float(n):,.2f}"
    msg = f"""<b>📊 ARK 链上数据 · {record['date']}</b>

━━━━━━━━━━━━━━━━━━━━━

<b>💰 奖金池</b>
余额：{f(record['bonus_balance'])} ARK
当日提取：<code>{f(record['bonus_withdraw'])}</code> ARK

<b>🔒 质押池</b>
余额：{f(record['stake_balance'])} ARK
新增质押：<code>{f(record['stake_in'])}</code> ARK
赎回：<code>{f(record['stake_out'])}</code> ARK
净质押：<b>{f(record['net_stake'])}</b> ARK

<b>⚡ 涡轮</b>
静态涡轮：{f(record.get('static_burn',0))} ARK
动态涡轮：{f(max(record.get('dynamic_in',0)-record.get('static_burn',0),0))} ARK
动静态涡轮：{f(record.get('dynamic_in',0))} ARK

━━━━━━━━━━━━━━━━━━━━━
<i>实时监控 · 每日汇总</i>"""
    reply_markup = {"inline_keyboard": [[{"text": "📊 打开看板", "url": "http://arkcy.duckdns.org/"}]]}
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML",
              "disable_web_page_preview": True, "reply_markup": reply_markup}, timeout=15)


def _send_today(record):
    """只推 Telegram，不写飞书"""
    from pusher import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    import requests
    def f(n): return f"{float(n):,.2f}"
    msg = f"""<b>📊 ARK 链上数据 · {record['date']}</b>

━━━━━━━━━━━━━━━━━━━━━

<b>💰 奖金池</b>
余额：{f(record['bonus_balance'])} ARK
当日提取：<code>{f(record['bonus_withdraw'])}</code> ARK

<b>🔒 质押池</b>
余额：{f(record['stake_balance'])} ARK
新增质押：<code>{f(record['stake_in'])}</code> ARK
赎回：<code>{f(record['stake_out'])}</code> ARK
净质押：<b>{f(record['net_stake'])}</b> ARK

<b>⚡ 涡轮</b>
静态涡轮：{f(record.get('static_burn',0))} ARK
动态涡轮：{f(max(record.get('dynamic_in',0)-record.get('static_burn',0),0))} ARK
动静态涡轮：{f(record.get('dynamic_in',0))} ARK

━━━━━━━━━━━━━━━━━━━━━
<i>实时监控 · 每日汇总</i>"""
    reply_markup = {"inline_keyboard": [[{"text": "📊 打开看板", "url": "http://arkcy.duckdns.org/"}]]}
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML",
              "disable_web_page_preview": True, "reply_markup": reply_markup}, timeout=15)


def _send_today(record):
    """只推 Telegram，不写飞书"""
    from pusher import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    import requests
    def f(n): return f"{float(n):,.2f}"
    msg = f"""<b>📊 ARK 链上数据 · {record['date']}</b>

━━━━━━━━━━━━━━━━━━━━━

<b>💰 奖金池</b>
余额：{f(record['bonus_balance'])} ARK
当日提取：<code>{f(record['bonus_withdraw'])}</code> ARK

<b>🔒 质押池</b>
余额：{f(record['stake_balance'])} ARK
新增质押：<code>{f(record['stake_in'])}</code> ARK
赎回：<code>{f(record['stake_out'])}</code> ARK
净质押：<b>{f(record['net_stake'])}</b> ARK

<b>⚡ 涡轮</b>
静态涡轮：{f(record.get('static_burn',0))} ARK
动态涡轮：{f(max(record.get('dynamic_in',0)-record.get('static_burn',0),0))} ARK
动静态涡轮：{f(record.get('dynamic_in',0))} ARK

━━━━━━━━━━━━━━━━━━━━━
<i>实时监控 · 每日汇总</i>"""
    reply_markup = {"inline_keyboard": [[{"text": "📊 打开看板", "url": "http://arkcy.duckdns.org/"}]]}
    requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML",
              "disable_web_page_preview": True, "reply_markup": reply_markup}, timeout=15)

def telegram_poll():
    """后台轮询 Telegram 消息，响应 /today 命令"""
    if not TELEGRAM_BOT_TOKEN:
        print("[Telegram Poll] 未配置 BOT_TOKEN，跳过")
        return
    offset = 0
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
            params = {"offset": offset, "timeout": 30, "allowed_updates": ["message"]}
            r = requests.get(url, params=params, timeout=35)
            d = r.json()
            if not d.get("ok"):
                time.sleep(5)
                continue
            for update in d.get("result", []):
                update_id = update.get("update_id", 0)
                offset = max(offset, update_id + 1)
                msg = update.get("message", {})
                chat_id = msg.get("chat", {}).get("id")
                text = msg.get("text", "").strip()
                # 只响应来自指定群组的 /today 命令
                if chat_id == TELEGRAM_CHAT_ID and text == "/today":
                    print(f"[Telegram Poll] 收到 /today 命令")
                    record = get_today_data()
                    _send_today(record)
        except Exception as e:
            print(f"[Telegram Poll] 异常: {e}")
            time.sleep(5)

# 启动后台轮询
threading.Thread(target=telegram_poll, daemon=True).start()

@app.get("/api/today")
def get_today():
    return {"data": get_today_data()}

@app.get("/api/daily")
def get_daily():
    return {"data": get_all_daily_until_yesterday(), "count": 0}

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
