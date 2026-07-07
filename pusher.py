#!/usr/bin/env python3
"""
推送工具：飞书多维表格 + Telegram
- 由汇总逻辑在每天结束时调用
"""

import os, sys, json, time, requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

BJT = timezone(timedelta(hours=8))

# 飞书配置
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_APP_TOKEN = "B5lBbWgjXamRS6s1CcEcTvgtnQc"
FEISHU_TABLE_ID = "tblVmNxjg8WjyXdw"

FIELD_MAP = {
    "bonus_balance": "奖金池余额",
    "bonus_withdraw": "奖金池提取",
    "static_burn": "静态涡轮",
    "dynamic_in": "动静态涡轮",
    "transfer_720": "转720天",
    "stake_balance": "质押池余额",
    "stake_in": "新增质押",
    "stake_out": "赎回",
    "net_stake": "净质押量",
    "pool_ark": "底池ARK",
    "pool_usdt": "底池USDT",
    "ark_price": "ARK价格",
}

FIELD_PRECISION = {
    "ark_price": 6,
}

# Telegram 配置
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_chat = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_CHAT_ID = int(_chat) if _chat.lstrip("-").isdigit() else _chat


def get_feishu_token():
    r = requests.post("https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": FEISHU_APP_ID, "app_secret": FEISHU_APP_SECRET}, timeout=10)
    d = r.json()
    if d.get("code") != 0: raise Exception(f"飞书 token 失败: {d}")
    return d["tenant_access_token"]


def push_to_feishu(record):
    """写入飞书多维表格（自动去重覆盖）"""
    token = get_feishu_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    date_str = record["date"]

    # 查找并删除旧记录
    url = f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}/tables/{FEISHU_TABLE_ID}/records"
    date_ms = int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=BJT).timestamp() * 1000)
    page_token = ""
    while True:
        params = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token
        r = requests.get(url, headers=headers, params=params, timeout=15)
        existing = r.json()
        if existing.get("code") != 0:
            print(f"  [飞书] 查询旧记录失败: {existing}")
            break
        for item in existing.get("data", {}).get("items", []):
            if item.get("fields", {}).get("日期") == date_ms:
                rid = item["record_id"]
                dr = requests.delete(f"{url}/{rid}", headers=headers, timeout=15).json()
                if dr.get("code") == 0:
                    print(f"  [飞书] 删除旧记录 {date_str}")
                    time.sleep(0.3)
        if not existing.get("data", {}).get("has_more"):
            break
        page_token = existing.get("data", {}).get("page_token", "")

    # 写入新记录
    ts = int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=BJT).timestamp() * 1000)
    fields = {"日期": ts}
    for key, val in record.items():
        if key in FIELD_MAP and key != "date" and val is not None:
            fields[FIELD_MAP[key]] = round(float(val), FIELD_PRECISION.get(key, 2))

    r = requests.post(url, headers=headers, json={"fields": fields}, timeout=15)
    d = r.json()
    if d.get("code") == 0:
        print(f"  [飞书] 写入成功 {date_str}")
    else:
        print(f"  [飞书] 写入失败: {d}")
    return d.get("code") == 0


def _fmt_720(r):
    return f"{float(r.get('transfer_720',0)):,.2f}"

def _fmt_price(r):
    return f"{float(r.get('ark_price',0)):,.6f}"

def push_to_telegram(record):
    """推送汇总到 Telegram"""
    if not TELEGRAM_BOT_TOKEN:
        print("  [Telegram] 跳过: 未配置 BOT_TOKEN")
        return False

    def f(n): return f"{float(n):,.2f}"

    msg = f"""<b>📊 ARK 链上数据 · {record['date']}</b>

━━━━━━━━━━━━━━━━

<b>💰 奖金池</b>
余额：{f(record['bonus_balance'])} ARK
当日提取：{f(record['bonus_withdraw'])} ARK

<b>🔒 质押池</b>
余额：{f(record['stake_balance'])} ARK
新增质押：{f(record['stake_in'])} ARK
赎回：{f(record['stake_out'])} ARK
净质押：{f(record['net_stake'])} ARK

<b>⚡ 涡轮</b>
静态涡轮：{f(record.get('static_burn',0))} ARK
动态涡轮：{f(max(record.get('dynamic_in',0)-record.get('static_burn',0),0))} ARK
动静态涡轮：{f(record.get('dynamic_in',0))} ARK

<b>🔄 转720天</b>
{_fmt_720(record)} ARK

<b>💧 底池</b>
ARK：{f(record.get('pool_ark',0))} ARK
USDT：{f(record.get('pool_usdt',0))} USDT
ARK价格：${_fmt_price(record)}

━━━━━━━━━━━━━━━━
📡 实时监控 · 每日汇总
🏷 数据由创亿社区提供"""

    reply_markup = {"inline_keyboard": [[{"text": "📊查看更多数据", "url": "http://arkcy.duckdns.org/"}]]}
    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML",
              "disable_web_page_preview": True, "reply_markup": reply_markup}, timeout=15)
    d = r.json()
    if d.get("ok"):
        print(f"  [Telegram] 推送成功 {record['date']}")
        return True
    else:
        print(f"  [Telegram] 推送失败: {d.get('description', d)}")
        return False
