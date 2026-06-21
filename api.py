#!/usr/bin/env python3
"""FastAPI 看板 API + 服务端渲染"""

import os
from datetime import datetime, timezone, timedelta
try:
    from fastapi import FastAPI, HTMLResponse
    from fastapi.middleware.cors import CORSMiddleware
except ImportError:
    FastAPI = None

from db import get_all_daily, get_conn
from event_parser import get_balance, BONUS_POOL, STAKE_POOL, TOKEN_ARK, DECIMALS
from pusher import push_to_feishu, push_to_telegram

BJT = timezone(timedelta(hours=8))

app = None
if FastAPI:
    app = FastAPI(title="ARK 实时监控")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

    def get_today_data():
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
            FROM events WHERE timestamp LIKE ?
        """, (today + "%",)).fetchone()
        conn.close()
        bonus_bal = get_balance(TOKEN_ARK, BONUS_POOL) / 10**DECIMALS
        stake_bal = get_balance(TOKEN_ARK, STAKE_POOL) / 10**DECIMALS

        bonus_out = float(row["bonus_out"]) if row["bonus_out"] else 0
        si = float(row["stake_in"]) if row["stake_in"] else 0
        so = float(row["stake_out"]) if row["stake_out"] else 0
        sb = float(row["static_burn"]) if row["static_burn"] else 0
        di = float(row["dynamic_in"]) if row["dynamic_in"] else 0
        ec = int(row["event_count"]) if row["event_count"] else 0
        lb = int(row["last_block"]) if row["last_block"] else 0
        ns = si - so - bonus_out

        return {
            "date": today, "bonus_balance": round(bonus_bal, 2),
            "bonus_withdraw": round(bonus_out, 2), "static_burn": round(sb, 2),
            "dynamic_in": round(di, 2), "dynamic_turbo": round(max(di - sb, 0), 2),
            "stake_balance": round(stake_bal, 2), "stake_in": round(si, 2),
            "stake_out": round(so, 2), "net_stake": round(ns, 2),
            "event_count": ec, "last_block": lb,
        }

    @app.get("/")
    def index():
        td = get_today_data()
        dd = get_all_daily()
        now = datetime.now(BJT).strftime("%Y-%m-%d %H:%M:%S")

        def f(n):
            if n is None: return "--"
            return f"{n:,.2f}"

        # Real-time 4 cards
        rc = f"""<div class="realtime-grid">
    <div class="rt-card blue"><div class="label">\u91d1\u94b1\u6c60\u4f59\u989d</div><div class="value">{f(td["bonus_balance"])}</div><div class="sub">ARK</div></div>
    <div class="rt-card green"><div class="label">\u8d28\u62bc\u6c60\u4f59\u989d</div><div class="value">{f(td["stake_balance"])}</div><div class="sub">ARK</div></div>
    <div class="rt-card orange"><div class="label">\u7d2f\u8ba1\u4e8b\u4ef6</div><div class="value">{td["event_count"]}</div><div class="sub">\u4eca\u65e5</div></div>
    <div class="rt-card purple"><div class="label">\u6700\u65b0\u533a\u5757</div><div class="value">{td["last_block"]}</div><div class="sub" id="lastBlockTime"></div></div>
  </div>"""

        # Today 6 cards
        tc = f"""<div class="today-card">
    <div class="today-title">\U0001f4ca \u5f53\u65e5\u5b9e\u65f6\u6c47\u603b \u00b7 {td["date"]}</div>
    <div class="today-grid" style="grid-template-columns: repeat(6, 1fr);">
      <div class="today-item warn"><div class="label">\u5956\u91d1\u6c60\u63d0\u53d6</div><div class="value">{f(td["bonus_withdraw"])}</div><div class="sub">ARK</div></div>
      <div class="today-item purple"><div class="label">\u9759\u6001\u6da1\u8f6e</div><div class="value">{f(td["static_burn"])}</div><div class="sub">gARK \u9500\u6bc1</div></div>
      <div class="today-item teal"><div class="label">\u52a8\u6001\u6da1\u8f6e</div><div class="value">{f(td["dynamic_turbo"])}</div><div class="sub">\u52a8\u9759\u6001-\u9759\u6001</div></div>
      <div class="today-item pos"><div class="label">\u65b0\u589e\u8d28\u62bc</div><div class="value">{f(td["stake_in"])}</div><div class="sub">ARK</div></div>
      <div class="today-item neg"><div class="label">\u8d4e\u56de</div><div class="value">{f(td["stake_out"])}</div><div class="sub">ARK</div></div>
      <div class="today-item info"><div class="label">\u51c0\u8d28\u62bc\u91cf</div><div class="value">{f(td["net_stake"])}</div><div class="sub">ARK</div></div>
    </div>
  </div>"""

        # History table
        rows = ""
        for r in dd:
            ns = r["net_stake"]
            cls = "pos" if ns >= 0 else "neg"
            turbo = max(r["dynamic_in"] - r["static_burn"], 0)
            rows += "<tr>"
            rows += f'<td>{r["date"]}</td><td>{f(r["bonus_balance"])}</td><td>{f(r["bonus_withdraw"])}</td>'
            rows += f'<td>{f(r["dynamic_in"])}</td><td>{f(r["static_burn"])}</td><td>{f(turbo)}</td>'
            rows += f'<td>{f(r["stake_balance"])}</td><td class="pos">{f(r["stake_in"])}</td><td class="neg">{f(r["stake_out"])}</td>'
            rows += f'<td class="{cls}">{f(ns)}</td></tr>'
        if not rows:
            rows = '<tr><td colspan="10" style="text-align:center;padding:20px;color:#64748b;">\u6682\u65e0\u5386\u53f2\u6570\u636e</td></tr>'

        # Events
        conn = get_conn()
        events = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT 50").fetchall()
        conn.close()
        TL = {"bonus_withdraw":"\u5956\u91d1\u6c60\u63d0\u53d6","stake_in":"\u65b0\u589e\u8d28\u62bc","stake_out":"\u8d4e\u56de","static_burn":"\u9759\u6001\u9500\u6bc1","dynamic":"\u52a8\u9759\u6001\u6da1\u8f6e"}
        ev = ""
        for e in events:
            label = TL.get(e["type"], e["type"])
            ts = e["timestamp"][11:19] if e["timestamp"] else ""
            txh = e["tx"][:10]+"..." if e["tx"] else ""
            ev += f'<div class="event-item"><span><span class="event-type {e["type"]}">{label}</span> #{e["block"]} {ts} <span style="color:#475569;font-size:11px;">{txh}</span></span><span style="font-weight:600;">{f(e["value"])} ARK</span></div>'
        if not ev:
            ev = '<div style="text-align:center;color:#64748b;padding:10px;">\u6682\u65e0\u4e8b\u4ef6</div>'

        return HTMLResponse(f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>ARK \u5b9e\u65f6\u76d1\u63a7\u770b\u677f</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;background:#0f172a;color:#e2e8f0;min-height:100vh}}
.header{{background:linear-gradient(135deg,#1e293b 0%,#0f172a 100%);padding:20px 24px;border-bottom:1px solid #334155;display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:12px}}
.header h1{{font-size:22px;font-weight:700}}.header h1 span{{color:#60a5fa}}
.header-status{{font-size:13px;color:#94a3b8}}.dot{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:6px;background:#22c55e}}
.container{{max-width:1200px;margin:0 auto;padding:20px}}
.realtime-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:24px}}
.rt-card{{background:#1e293b;border-radius:10px;padding:14px;text-align:center;border:1px solid #334155}}
.rt-card .label{{font-size:11px;color:#94a3b8;text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}}
.rt-card .value{{font-size:20px;font-weight:700}}.rt-card .sub{{font-size:11px;color:#64748b;margin-top:2px}}
.rt-card.blue .value{{color:#60a5fa}}.rt-card.green .value{{color:#22c55e}}
.rt-card.orange .value{{color:#f59e0b}}.rt-card.purple .value{{color:#a78bfa}}
.today-card{{background:#1e293b;border-radius:12px;padding:20px;margin-bottom:20px;border:1px solid #334155}}
.today-title{{font-size:14px;font-weight:600;color:#94a3b8;margin-bottom:16px}}
.today-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px}}
.today-item{{background:#0f172a;border-radius:8px;padding:12px}}
.today-item .label{{font-size:11px;color:#64748b;margin-bottom:4px}}.today-item .value{{font-size:18px;font-weight:700}}
.today-item.pos .value{{color:#22c55e}}.today-item.neg .value{{color:#ef4444}}
.today-item.warn .value{{color:#f59e0b}}.today-item.info .value{{color:#60a5fa}}
.today-item.purple .value{{color:#a78bfa}}.today-item.teal .value{{color:#2dd4bf}}
.section-title{{font-size:16px;font-weight:600;margin:20px 0 10px;color:#cbd5e1}}
.table-wrap{{overflow-x:auto;background:#1e293b;border-radius:12px;border:1px solid #334155}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
thead th{{background:#0f172a;color:#94a3b8;font-weight:600;padding:12px 10px;text-align:right;border-bottom:1px solid #334155;white-space:nowrap}}
thead th:first-child{{text-align:left}}
tbody td{{padding:10px;text-align:right;border-bottom:1px solid #1a2332;white-space:nowrap}}
tbody td:first-child{{text-align:left;color:#94a3b8;font-weight:500}}
tbody tr:hover{{background:#1a2332}}
td.pos{{color:#22c55e}}td.neg{{color:#ef4444}}
.events-box{{background:#1e293b;border-radius:12px;border:1px solid #334155;margin-top:20px;padding:16px;max-height:300px;overflow-y:auto}}
.events-box .event-item{{padding:6px 0;border-bottom:1px solid #1a2332;font-size:12px;display:flex;justify-content:space-between}}
.events-box .event-item:last-child{{border:none}}
.event-type{{display:inline-block;padding:1px 6px;border-radius:4px;font-size:10px;font-weight:600;margin-right:6px}}
.event-type.bonus_withdraw{{background:#450a0a;color:#fca5a5}}
.event-type.stake_in{{background:#052e16;color:#86efac}}
.event-type.stake_out{{background:#422006;color:#fcd34d}}
.event-type.static_burn{{background:#1a052e;color:#d8b4fe}}
.event-type.dynamic{{background:#0c4a6e;color:#7dd3fc}}
.footer{{text-align:center;padding:20px;color:#475569;font-size:12px}}</style></head><body>
<div class="header"><div><h1><span>ARK</span> \u5b9e\u65f6\u76d1\u63a7</h1><div class="header-status"><span class="dot"></span>\u8fd0\u884c\u4e2d</div></div><div style="display:flex;gap:12px;align-items:center"><span style="font-size:12px;color:#64748b">{now}</span></div></div>
<div class="container">{rc}{tc}
<div class="section-title">\U0001f4c8 \u5386\u53f2\u6570\u636e\uff08\u8fd130\u5929\uff09</div>
<div class="table-wrap"><table><thead><tr><th>\u65e5\u671f</th><th style="color:#60a5fa">\u5956\u91d1\u6c60\u4f59\u989d</th><th style="color:#f59e0b">\u5956\u91d1\u6c60\u63d0\u53d6</th><th style="color:#7dd3fc">\u52a8\u9759\u6001\u6da1\u8f6e</th><th style="color:#a78bfa">\u9759\u6001\u6da1\u8f6e</th><th style="color:#14b8a6">\u52a8\u6001\u6da1\u8f6e</th><th style="color:#94a3b8">\u8d28\u62bc\u6c60\u4f59\u989d</th><th style="color:#22c55e">\u65b0\u589e\u8d28\u62bc</th><th style="color:#ef4444">\u8d4e\u56de</th><th style="color:#f97316">\u51c0\u8d28\u62bc\u91cf</th></tr></thead><tbody>{rows}</tbody></table></div>
<div class="section-title">\u26a1 \u6700\u65b0\u4e8b\u4ef6\u6d41</div><div class="events-box">{ev}</div></div>
<div class="footer">ARK \u5b9e\u65f6\u76d1\u63a7 \u00b7 \u6570\u636e\u6765\u6e90 BSC \u94fe\u4e0a</div></body></html>""")

    @app.get("/api/today")
    def get_today():
        return {"data": get_today_data()}

    @app.get("/api/balances")
    def get_balances():
        bonus_bal = get_balance(TOKEN_ARK, BONUS_POOL) / 10**DECIMALS
        stake_bal = get_balance(TOKEN_ARK, STAKE_POOL) / 10**DECIMALS
        return {"bonus_balance": round(bonus_bal, 2), "stake_balance": round(stake_bal, 2)}

    @app.get("/api/daily")
    def get_daily():
        data = get_all_daily()
        return {"data": data, "count": len(data)}

    @app.get("/api/realtime")
    def get_realtime(limit: int = 100):
        conn = get_conn()
        rows = conn.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        conn.close()
        return {"data": [dict(r) for r in rows]}

    @app.get("/api/force-summary")
    def force_summary():
        record = get_today_data()
        push_to_feishu(record)
        push_to_telegram(record)
        return {"status": "ok", "data": record}

    @app.get("/health")
    def health():
        return {"status": "ok", "time": datetime.now(BJT).isoformat()}
