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
    "stake_balance": "质押池余额",
    "stake_in": "新增质押",
    "stake_out": "赎回",
    "net_stake": "净质押量",
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
    r = requests.get(url + "?page_size=20&field_names=日期", headers=headers, timeout=15)
    existing = r.json()
    if existing.get("code") == 0:
        date_ms = int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=BJT).timestamp() * 1000)
        for item in existing.get("data", {}).get("items", []):
            if item.get("fields", {}).get("日期") == date_ms:
                rid = item["record_id"]
                dr = requests.delete(f"{url}/{rid}", headers=headers, timeout=15).json()
                if dr.get("code") == 0:
                    print(f"  [飞书] 删除旧记录 {date_str}")
                    time.sleep(0.3)

    # 写入新记录
    ts = int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=BJT).timestamp() * 1000)
    fields = {"日期": ts}
    for key, val in record.items():
        if key in FIELD_MAP and key != "date":
            fields[FIELD_MAP[key]] = round(float(val), 2)

    r = requests.post(url, headers=headers, json={"fields": fields}, timeout=15)
    d = r.json()
    if d.get("code") == 0:
        print(f"  [飞书] 写入成功 {date_str}")
    else:
        print(f"  [飞书] 写入失败: {d}")
    return d.get("code") == 0


def push_to_telegram(record):
    """推送汇总到 Telegram"""
    if not TELEGRAM_BOT_TOKEN:
        print("  [Telegram] 跳过: 未配置 BOT_TOKEN")
        return False

    def f(n): return f"{float(n):,.2f}"

    msg = f"""<b>📊 ARK 链上数据 · {record['date']}</b>

━━━━━━━━━━━━━━━━━━

<b>💰 奖金池</b>
余额：{f(record['bonus_balance'])} ARK
当日提取：<code>{f(record['bonus_withdraw'])}</code> ARK

<b>🔒 质押池</b>
余额：{f(record['stake_balance'])} ARK
新增质押：<code>{f(record['stake_in'])}</code> ARK
赎回：<code>{f(record['stake_out'])}</code> ARK
净质押：<b>{f(record['net_stake'])}</b> ARK

<b>⚡ 涡轮</b>
静态（gARK销毁）：{f(record.get('static_burn',0))} ARK
动静态：{f(record.get('dynamic_in',0))} ARK

━━━━━━━━━━━━━━━━━━
<i>实时监控 · 每日汇总</i>"""

    r = requests.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "HTML",
              "disable_web_page_preview": True}, timeout=15)
    d = r.json()
    if d.get("ok"):
        print(f"  [Telegram] 推送成功 {record['date']}")
        return True
    else:
        print(f"  [Telegram] 推送失败: {d.get('description', d)}")
        return False
