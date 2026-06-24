#!/usr/bin/env python3
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import os, threading, time, requests
from datetime import datetime, timezone, timedelta
from db import get_all_daily_until_yesterday, get_conn
from event_parser import BONUS_POOL, STAKE_POOL, TOKEN_ARK, DECIMALS
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
    yesterday = (datetime.now(BJT) - timedelta(days=1)).strftime("%Y-%m-%d")
    conn = get_conn()

    # 取昨日汇总作为余额基准
    yd = conn.execute("SELECT * FROM daily_summary WHERE date=?", (yesterday,)).fetchone()
    base_bonus = float(yd["bonus_balance"]) if yd else 0
    base_stake = float(yd["stake_balance"]) if yd else 0

    # 取今日事件汇总
    row = conn.execute("""
        SELECT COALESCE(SUM(CASE WHEN type='bonus_withdraw' THEN value ELSE 0 END),0),
               COALESCE(SUM(CASE WHEN type='stake_in' THEN value ELSE 0 END),0),
               COALESCE(SUM(CASE WHEN type='stake_out' THEN value ELSE 0 END),0),
               COALESCE(SUM(CASE WHEN type='static_burn' THEN value ELSE 0 END),0),
               COALESCE(SUM(CASE WHEN type='dynamic' THEN value ELSE 0 END),0),
               COALESCE(SUM(CASE WHEN type='transfer_720' THEN value ELSE 0 END),0),
               COUNT(*), MAX(block)
        FROM events WHERE timestamp LIKE ?
    """, (today + "%",)).fetchone()
    conn.close()

    bo = float(row[0]) if row[0] else 0
    si = float(row[1]) if row[1] else 0
    so = float(row[2]) if row[2] else 0
    sb = float(row[3]) if row[3] else 0
    di = float(row[4]) if row[4] else 0
    tr720 = float(row[5]) if row[5] else 0
    ec = int(row[6]) if row[6] else 0
    lb = int(row[7]) if row[7] else 0

    # 估算当前余额
    bonus_bal = base_bonus - bo - tr720
    stake_bal = base_stake + si + tr720 - so

    return {"date":today,"bonus_balance":round(max(bonus_bal,0),2),"bonus_withdraw":round(bo,2),
            "static_burn":round(sb,2),"dynamic_in":round(di,2),
            "dynamic_turbo":round(max(di-sb,0),2),"transfer_720":round(tr720,2),"stake_balance":round(max(stake_bal,0),2),
            "stake_in":round(si,2),"stake_out":round(so,2),"net_stake":round(si-so,2),
            "event_count":ec,"last_block":lb}


def get_today_trend():
    '''最近24小时逐小时趋势'''
    cutoff = (datetime.now(BJT) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    q = '''SELECT SUBSTR(REPLACE(timestamp, 'T', ' '), 1, 13) as hour_label,
        COALESCE(SUM(CASE WHEN type='bonus_withdraw' THEN value ELSE 0 END),0) as bonus_out,
        COALESCE(SUM(CASE WHEN type='static_burn' THEN value ELSE 0 END),0) as static_burn,
        COALESCE(SUM(CASE WHEN type='dynamic' THEN value ELSE 0 END),0) as dynamic_in,
        COALESCE(SUM(CASE WHEN type='transfer_720' THEN value ELSE 0 END),0) as transfer_720,
        COALESCE(SUM(CASE WHEN type='stake_in' THEN value ELSE 0 END),0) as stake_in_val,
        COALESCE(SUM(CASE WHEN type='stake_out' THEN value ELSE 0 END),0) as stake_out_val
        FROM events WHERE timestamp >= ? GROUP BY hour_label ORDER BY hour_label'''
    rows = conn.execute(q, (cutoff,)).fetchall()
    conn.close()
    result = []
    for r in rows:
        hl = r[0]
        bo = float(r[1]) if r[1] else 0
        sb = float(r[2]) if r[2] else 0
        di = float(r[3]) if r[3] else 0
        t720 = float(r[4]) if r[4] else 0
        si2 = float(r[5]) if r[5] else 0
        so2 = float(r[6]) if r[6] else 0
        # hour_label = "2026-06-24 13" -> "06/24 13:00"
        try:
            parts = hl.split(" ")
            date_part = parts[0][5:]
            hour_part = parts[1]
            display = date_part.replace("-", "/") + " " + hour_part + ":00"
        except:
            display = str(hl)
        result.append({'hour': display, 'bonus_withdraw': round(bo, 2),
                       'static_burn': round(sb, 2),
                       'dynamic_turbo': round(max(di - sb, 0), 2),
                       'transfer_720': round(float(r[4]) if r[4] else 0, 2),
                       'stake_in': round(float(r[5]) if r[5] else 0, 2),
                       'stake_out': round(float(r[6]) if r[6] else 0, 2)})
    return result


# ---------- Telegram /today 命令轮询 ----------
def _send_today(record):
    """只推 Telegram，不写飞书"""
    from pusher import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
    import requests
    def f(n): return f"{float(n):,.2f}"
    def fmt_720(r): return f(round(float(r.get('transfer_720',0)), 2))
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

<b>🔄 转720天</b>
{fmt_720(record)} ARK

━━━━━━━━━━━━━━━━━━━━━
<i>实时监控 · 每日汇总</i>"""
    reply_markup = {"inline_keyboard": [[{"text": "📊查看更多数据", "url": "http://arkcy.duckdns.org/"}]]}
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


@app.get("/api/today-trend")
def get_today_trend_api():
    return {"data": get_today_trend()}

@app.get("/api/daily")
def get_daily():
    conn = get_conn()
    today = datetime.now(BJT).strftime("%Y-%m-%d")
    rows = conn.execute("SELECT * FROM daily_summary WHERE date < ? ORDER BY date DESC LIMIT 30", (today,)).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        # 补充 transfer_720（DB 中可能没有此列）
        tr720 = conn.execute("SELECT COALESCE(SUM(value),0) FROM events WHERE type='transfer_720' AND timestamp LIKE ?", (d['date'] + '%',)).fetchone()[0]
        d['transfer_720'] = round(float(tr720), 2)
        result.append(d)
    conn.close()
    return {"data": result, "count": len(result)}



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
