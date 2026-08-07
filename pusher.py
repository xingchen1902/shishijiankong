#!/usr/bin/env python3
"""
推送工具：飞书多维表格 + Telegram
- 由汇总逻辑在每天结束时调用
"""

import os, sys, json, time, requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
from db import get_pool_address_daily_summaries
from event_parser import get_balance, TOKEN_USDT, DECIMALS

load_dotenv()

BJT = timezone(timedelta(hours=8))

# 飞书配置
FEISHU_APP_ID = os.environ.get("FEISHU_APP_ID", "")
FEISHU_APP_SECRET = os.environ.get("FEISHU_APP_SECRET", "")
FEISHU_APP_TOKEN = "B5lBbWgjXamRS6s1CcEcTvgtnQc"
FEISHU_TABLE_ID = "tblVmNxjg8WjyXdw"
FEISHU_STAKING_TABLE_ID = "tblOCpFwZ3a5LCJJ"

FIELD_MAP = {
    "bonus_balance": "奖金池余额",
    "bonus_withdraw": "奖金池提取",
    "static_burn": "静态释放",
    "dynamic_release": "动态释放",
    "dynamic_in": "总涡轮",
    "transfer_720": "转720天",
    "stake_balance": "质押池余额",
    "stake_in": "新增质押",
    "burn_stake": "销毁质押",
    "stake_out": "赎回",
    "net_stake": "净质押量",
    "permanent_stake": "本金永久质押",
    "permanent_bonus": "收益永久质押",
    "pool_ark": "底池ARK",
    "pool_usdt": "底池USDT",
    "ark_price": "ARK价格",
    "buy_value_usdt": "买入价值",
    "sell_value_usdt": "卖出价值",
}

FIELD_PRECISION = {
    "ark_price": 6,
}

# Telegram 配置
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
_chat = os.environ.get("TELEGRAM_CHAT_ID", "")
TELEGRAM_CHAT_ID = int(_chat) if _chat.lstrip("-").isdigit() else _chat
_chat_ids = os.environ.get("TELEGRAM_CHAT_IDS", "")
_no_button_chat_ids = os.environ.get("TELEGRAM_NO_BUTTON_CHAT_IDS", "")

def _parse_chat_id(value):
    value = str(value).strip()
    return int(value) if value.lstrip("-").isdigit() else value

def _parse_chat_ids(value):
    return [_parse_chat_id(item) for item in value.split(",") if item.strip()]

def get_telegram_chat_ids():
    chat_ids = _parse_chat_ids(_chat_ids) if _chat_ids else []
    if TELEGRAM_CHAT_ID and TELEGRAM_CHAT_ID not in chat_ids:
        chat_ids.insert(0, TELEGRAM_CHAT_ID)
    return chat_ids

def get_telegram_no_button_chat_ids():
    return set(_parse_chat_ids(_no_button_chat_ids))

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


def push_staking_snapshot_to_feishu(snapshot):
    """写入官网周期质押每日快照表，同一天覆盖旧记录。"""
    token = get_feishu_token()
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    date_str = snapshot["date"]
    url = (
        f"https://open.feishu.cn/open-apis/bitable/v1/apps/{FEISHU_APP_TOKEN}"
        f"/tables/{FEISHU_STAKING_TABLE_ID}/records"
    )
    date_ms = int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=BJT).timestamp() * 1000)

    page_token = ""
    while True:
        params = {"page_size": 100}
        if page_token:
            params["page_token"] = page_token
        existing = requests.get(url, headers=headers, params=params, timeout=15).json()
        if existing.get("code") != 0:
            print(f"  [飞书质押快照] 查询旧记录失败: {existing}")
            return False
        for item in existing.get("data", {}).get("items", []):
            if item.get("fields", {}).get("日期") == date_ms:
                deleted = requests.delete(f"{url}/{item['record_id']}", headers=headers, timeout=15).json()
                if deleted.get("code") != 0:
                    print(f"  [飞书质押快照] 删除旧记录失败: {deleted}")
                    return False
        if not existing.get("data", {}).get("has_more"):
            break
        page_token = existing.get("data", {}).get("page_token", "")

    fields = {"日期": date_ms}
    field_map = {
        3: ("180天单币质押", "180天债券质押"),
        4: ("360天单币质押", "360天债券质押"),
        5: ("540天单币质押", "540天债券质押"),
        6: ("720天单币质押", "720天债券质押"),
        100: ("永久单币质押", "永久债券质押"),
    }
    use_baseline = date_str == "2026-08-04"
    for row in snapshot.get("data", {}).get("data", []):
        names = field_map.get(int(row.get("mode_id", 0)))
        if names:
            staking_key = "staking_ark" if use_baseline else "staking_change"
            bond_key = "bond_ark" if use_baseline else "bond_change"
            fields[names[0]] = round(float(row.get(staking_key) or 0), 6)
            fields[names[1]] = round(float(row.get(bond_key) or 0), 6)
    total_key = "staked_total_ark" if use_baseline else "total_change"
    if use_baseline:
        total = snapshot.get("data", {}).get(total_key, 0)
    else:
        total = sum(float(row.get(total_key) or 0) for row in snapshot.get("data", {}).get("data", []))
    fields["总质押"] = round(float(total), 6)

    address_rows = {
        row["name"]: row
        for row in get_pool_address_daily_summaries(365)
        if row.get("date") == date_str
    }
    for name, field_name in (("MBR资金", "MBR变化"), ("RBS资金", "RBS变化")):
        row = address_rows.get(name)
        if row:
            fields[field_name] = round(
                float(row.get("usdt_to_pool") or 0) - float(row.get("usdt_from_pool") or 0), 6
            )
    treasury_address = "0x1b9f458773d18b4e1aaf5b896721697215c4a68b"
    fields["国库"] = round(get_balance(TOKEN_USDT, treasury_address) / (10 ** DECIMALS), 6)
    fee_row = address_rows.get("手续费")
    if fee_row:
        fields["手续费卖出"] = round(float(fee_row.get("usdt_from_pool") or 0), 6)

    result = requests.post(url, headers=headers, json={"fields": fields}, timeout=15).json()
    if result.get("code") == 0:
        print(f"  [飞书质押快照] 写入成功 {date_str}")
        return True
    print(f"  [飞书质押快照] 写入失败: {result}")
    return False


def _fmt_720(r):
    return f"{float(r.get('transfer_720',0)):,.2f}"

def _fmt_price(r):
    return f"{float(r.get('ark_price',0)):,.6f}"

def _fmt_delta(value, decimals=2):
    n = float(value or 0)
    return f"{n:+,.{decimals}f}"

def push_to_telegram(record, target_chat_id=None, title_suffix="汇总"):
    """推送汇总到 Telegram"""
    if not TELEGRAM_BOT_TOKEN:
        print("  [Telegram] 跳过: 未配置 BOT_TOKEN")
        return False

    def f(n): return f"{float(n):,.2f}"

    msg = f"""<b>📊 ARK 链上数据</b>
<b>{record['date']} {title_suffix}</b>

━━━━━━━━━━━━━━━━

<b>💰 奖金池</b>
余额：{f(record['bonus_balance'])} ARK
当日提取：{f(record['bonus_withdraw'])} ARK

<b>🔒 质押池</b>
余额：{f(record['stake_balance'])} ARK
新增质押：{f(record['stake_in'])} ARK
新增销毁质押：{f(record.get('burn_stake',0))} ARK
赎回：{f(record['stake_out'])} ARK
净质押：{f(record['net_stake'])} ARK

<b>🔥 升级永久质押</b>
本金永久质押：{f(record.get('permanent_stake',0))} ARK
收益永久质押：{f(record.get('permanent_bonus',0))} ARK

<b>⚡ 涡轮</b>
静态释放：{f(record.get('static_burn',0))} ARK
动态释放：{f(record.get('dynamic_release', record.get('dynamic_turbo',0)))} ARK
总涡轮：{f(record.get('dynamic_in',0))} ARK

<b>🔄 转720天</b>
{_fmt_720(record)} ARK

<b>💧 底池</b>
ARK：{f(record.get('pool_ark',0))} ARK
较昨日：{_fmt_delta(record.get('pool_ark_delta'))} ARK

USDT：{f(record.get('pool_usdt',0))} USDT
较昨日：{_fmt_delta(record.get('pool_usdt_delta'))} USDT

ARK价格：${_fmt_price(record)}
较昨日：{_fmt_delta(record.get('ark_price_delta'), 6)}

━━━━━━━━━━━━━━━━
📡 实时监控 · {title_suffix}
"""

    chat_ids = [target_chat_id] if target_chat_id is not None else get_telegram_chat_ids()
    no_button_chat_ids = get_telegram_no_button_chat_ids()
    ok = True
    for chat_id in chat_ids:
        payload = {
            "chat_id": chat_id,
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if chat_id not in no_button_chat_ids:
            payload["reply_markup"] = {
                "inline_keyboard": [[{"text": "📊查看更多数据", "url": "http://arkcy.duckdns.org/"}]]
            }
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json=payload,
            timeout=15,
        )
        d = r.json()
        if d.get("ok"):
            print(f"  [Telegram] 推送成功 {record['date']} chat_id={chat_id}")
        else:
            ok = False
            print(f"  [Telegram] 推送失败 chat_id={chat_id}: {d.get('description', d)}")
    return ok


def push_burst_alert(message):
    """向已配置的 Telegram 会话发送集中事件提醒，不发送飞书。"""
    if not TELEGRAM_BOT_TOKEN:
        print("  [Telegram集中提醒] 跳过: 未配置 BOT_TOKEN")
        return False
    chat_ids = get_telegram_chat_ids()
    if not chat_ids:
        print("  [Telegram集中提醒] 跳过: 未配置 CHAT_ID")
        return False
    ok = True
    for chat_id in chat_ids:
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        try:
            response = requests.post(
                f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
                json=payload,
                timeout=15,
            )
            data = response.json()
            if data.get("ok"):
                print(f"  [Telegram集中提醒] 推送成功 chat_id={chat_id}")
            else:
                ok = False
                print(f"  [Telegram集中提醒] 推送失败 chat_id={chat_id}: {data.get('description', data)}")
        except Exception as exc:
            ok = False
            print(f"  [Telegram集中提醒] 请求失败 chat_id={chat_id}: {exc}")
    return ok
