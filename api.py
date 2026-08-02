#!/usr/bin/env python3
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
import os, threading, time, requests
from datetime import datetime, timezone, timedelta
from db import (
    get_all_daily_until_yesterday,
    get_conn,
    get_dex_daily_snapshot,
    get_dex_daily_snapshots,
    init_db,
    upsert_dex_daily_snapshot,
)
from event_parser import BONUS_POOL, STAKE_POOL, TOKEN_ARK, DECIMALS, get_balance
from pusher import (
    get_telegram_chat_ids,
    push_to_feishu,
    push_to_telegram,
    TELEGRAM_BOT_TOKEN,
    TELEGRAM_CHAT_ID,
)

BJT = timezone(timedelta(hours=8))
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEX_CACHE_TTL = 45
DEX_MIN_LIQUIDITY_USD = 1000
DEX_PRIMARY_PAIR = "0xcaaf3c41a40103a23eeaa4bba468af3cf5b0e0d8"
TOKEN_USDT = "0x55d398326f99059ff775485246999027b3197955"
DEX_CACHE = {"ts": 0, "data": None}
BONUS_BALANCE_CACHE = {"ts": 0, "value": None}
# 看板每 30 秒轮询一次；短缓存可避免多个浏览器/机器人同时重复扫描大表。
TODAY_CACHE_TTL = 8
TREND_CACHE_TTL = 45
TODAY_CACHE = {"date": None, "ts": 0, "data": None}
TREND_CACHE = {"ts": 0, "data": None}
DEX_SNAPSHOT_LOCK = threading.Lock()
app = FastAPI(title="ARK")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])
init_db()
import os.path
logo_path = os.path.join(BASE_DIR, "logo.png")
if os.path.exists(logo_path):
    @app.get("/logo.png")
    def get_logo():
        return FileResponse(logo_path, media_type="image/png")

def get_today_data():
    today = datetime.now(BJT).strftime("%Y-%m-%d")
    now_ts = time.time()
    if (TODAY_CACHE["date"] == today and TODAY_CACHE["data"] is not None
            and now_ts - TODAY_CACHE["ts"] < TODAY_CACHE_TTL):
        return dict(TODAY_CACHE["data"])
    yesterday = (datetime.now(BJT) - timedelta(days=1)).strftime("%Y-%m-%d")
    tomorrow = (datetime.now(BJT) + timedelta(days=1)).strftime("%Y-%m-%d")
    conn = get_conn()

    # 取昨日汇总作为余额基准
    yd = conn.execute("SELECT * FROM daily_summary WHERE date=?", (yesterday,)).fetchone()
    base_bonus = float(yd["bonus_balance"]) if yd else 0
    base_stake = float(yd["stake_balance"]) if yd else 0

    # 取今日事件汇总
    row = conn.execute("""
        SELECT COALESCE(SUM(CASE WHEN lower(from_addr)='0x8501168656fcac4628f6910ccabea8b64ebe5bd4' THEN value ELSE 0 END),0),
               COALESCE(SUM(CASE WHEN type='stake_in' THEN value ELSE 0 END),0),
               COALESCE(SUM(CASE WHEN lower(from_addr)='0xd1d95292f450b665566df4c4255615ef4ed9bd0b' THEN value ELSE 0 END),0),
               COALESCE(SUM(CASE WHEN type='release_static' THEN value ELSE 0 END),0),
               COALESCE(SUM(CASE WHEN type='turbo_total' THEN value ELSE 0 END),0),
               COALESCE(SUM(CASE WHEN type='release_dynamic' THEN value ELSE 0 END),0),
               COALESCE(SUM(CASE WHEN type='burn_stake' AND lower(from_addr) NOT IN ('0x7736b5b84caddb7661d250d10e60e31f3c905c99','0x100844ccd4af887d123c0ac4a9671e0ab5dd9de2','0x8501168656fcac4628f6910ccabea8b64ebe5bd4','0xd1d95292f450b665566df4c4255615ef4ed9bd0b') THEN value ELSE 0 END),0),
               COALESCE(SUM(CASE WHEN lower(from_addr)='0x8501168656fcac4628f6910ccabea8b64ebe5bd4' AND lower(to_addr)='0x0000000000000000000000000000000000000000' THEN value ELSE 0 END),0),
               COALESCE(SUM(CASE WHEN lower(from_addr)='0xd1d95292f450b665566df4c4255615ef4ed9bd0b' AND lower(to_addr)='0x0000000000000000000000000000000000000000' THEN value ELSE 0 END),0),
              COALESCE(SUM(CASE WHEN type='transfer_720' THEN value ELSE 0 END),0),
               COALESCE(SUM(CASE WHEN type='bonus_in' THEN value ELSE 0 END),0),
              COUNT(*), MAX(block)
        FROM events
        WHERE timestamp >= ? AND timestamp < ?
    """, (today + " 00:00:00", tomorrow + " 00:00:00")).fetchone()
    conn.close()

    raw_bo = float(row[0]) if row[0] else 0
    si = float(row[1]) if row[1] else 0
    raw_so = float(row[2]) if row[2] else 0
    sb = float(row[3]) if row[3] else 0
    di = float(row[4]) if row[4] else 0
    dynamic_release = float(row[5]) if row[5] else 0
    burn_stake = float(row[6]) if row[6] else 0
    permanent_bonus = float(row[7]) if row[7] else 0
    permanent_stake = float(row[8]) if row[8] else 0
    tr720 = float(row[9]) if row[9] else 0
    bi = float(row[10]) if row[10] else 0
    ec = int(row[11]) if row[11] else 0
    lb = int(row[12]) if row[12] else 0
    # 地址级统计全部转出，再扣除对应的永久质押黑洞转账。
    bo = max(raw_bo - permanent_bonus - tr720, 0)
    so = max(raw_so - permanent_stake, 0)
    real_stake_in = si + burn_stake

    # 事件公式保留用于校验；当前看板余额以链上 ARK balanceOf 为准，避免历史汇总基准误差累积。
    bonus_bal = base_bonus + bi - bo - permanent_bonus - tr720
    stake_bal = base_stake + si + tr720 - so - permanent_stake
    # 页面 30 秒刷新一次；余额缓存同步延长到 30 秒，多个看板/机器人请求共用结果。
    if BONUS_BALANCE_CACHE["value"] is None or now_ts - BONUS_BALANCE_CACHE["ts"] >= 30:
        try:
            BONUS_BALANCE_CACHE["value"] = get_balance(TOKEN_ARK, BONUS_POOL) / (10 ** DECIMALS)
            BONUS_BALANCE_CACHE["ts"] = now_ts
        except Exception as exc:
            print(f"[链上余额] 查询奖金池失败，使用事件公式: {exc}")
    displayed_bonus_bal = BONUS_BALANCE_CACHE["value"] if BONUS_BALANCE_CACHE["value"] is not None else bonus_bal

    result = {"date":today,"bonus_balance":round(max(displayed_bonus_bal,0),2),"bonus_balance_formula":round(max(bonus_bal,0),2),"bonus_withdraw":round(bo,2),
            "static_burn":round(sb,2),"dynamic_in":round(di,2),
            "burn_stake":round(burn_stake,2),
            "permanent_bonus":round(permanent_bonus,2),"permanent_stake":round(permanent_stake,2),
            "dynamic_turbo":round(dynamic_release,2),"dynamic_release":round(dynamic_release,2),"transfer_720":round(tr720,2),"stake_balance":round(max(stake_bal,0),2),
            "stake_in_raw":round(si,2),"stake_in":round(real_stake_in,2),"burn_stake":round(burn_stake,2),"stake_out":round(so,2),"net_stake":round(real_stake_in-so,2),
            "event_count":ec,"last_block":lb}
    TODAY_CACHE.update({"date": today, "ts": now_ts, "data": result})
    return dict(result)


def get_today_trend():
    '''最近24小时逐小时趋势'''
    now_ts = time.time()
    if TREND_CACHE["data"] is not None and now_ts - TREND_CACHE["ts"] < TREND_CACHE_TTL:
        return [dict(row) for row in TREND_CACHE["data"]]
    cutoff = (datetime.now(BJT) - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    q = '''SELECT SUBSTR(timestamp, 1, 13) as hour_label,
        COALESCE(SUM(CASE WHEN lower(from_addr)='0x8501168656fcac4628f6910ccabea8b64ebe5bd4' THEN value ELSE 0 END),0) as bonus_out_all,
        COALESCE(SUM(CASE WHEN type='release_static' THEN value ELSE 0 END),0) as static_burn,
        COALESCE(SUM(CASE WHEN type='turbo_total' THEN value ELSE 0 END),0) as dynamic_in,
        COALESCE(SUM(CASE WHEN type='release_dynamic' THEN value ELSE 0 END),0) as dynamic_release,
        COALESCE(SUM(CASE WHEN type='burn_stake' AND lower(from_addr) NOT IN ('0x7736b5b84caddb7661d250d10e60e31f3c905c99','0x100844ccd4af887d123c0ac4a9671e0ab5dd9de2','0x8501168656fcac4628f6910ccabea8b64ebe5bd4','0xd1d95292f450b665566df4c4255615ef4ed9bd0b') THEN value ELSE 0 END),0) as burn_stake,
        COALESCE(SUM(CASE WHEN type='transfer_720' THEN value ELSE 0 END),0) as transfer_720,
        COALESCE(SUM(CASE WHEN lower(from_addr)='0x8501168656fcac4628f6910ccabea8b64ebe5bd4' AND lower(to_addr)='0x0000000000000000000000000000000000000000' THEN value ELSE 0 END),0) as permanent_bonus,
        COALESCE(SUM(CASE WHEN lower(from_addr)='0xd1d95292f450b665566df4c4255615ef4ed9bd0b' AND lower(to_addr)='0x0000000000000000000000000000000000000000' THEN value ELSE 0 END),0) as permanent_stake,
        COALESCE(SUM(CASE WHEN type='stake_in' THEN value ELSE 0 END),0) as stake_in_val,
        COALESCE(SUM(CASE WHEN lower(from_addr)='0xd1d95292f450b665566df4c4255615ef4ed9bd0b' THEN value ELSE 0 END),0) as stake_out_all
        FROM events
        WHERE timestamp >= ?
        GROUP BY hour_label ORDER BY hour_label'''
    rows = conn.execute(q, (cutoff,)).fetchall()
    conn.close()
    result = []
    for r in rows:
        hl = r[0]
        bo_all = float(r[1]) if r[1] else 0
        sb = float(r[2]) if r[2] else 0
        di = float(r[3]) if r[3] else 0
        dynamic_release2 = float(r[4]) if r[4] else 0
        burn_stake2 = float(r[5]) if r[5] else 0
        t720 = float(r[6]) if r[6] else 0
        permanent_bonus2 = float(r[7]) if r[7] else 0
        permanent_stake2 = float(r[8]) if r[8] else 0
        si2 = float(r[9]) if r[9] else 0
        so_all = float(r[10]) if r[10] else 0
        bo = max(bo_all - permanent_bonus2 - t720, 0)
        so2 = max(so_all - permanent_stake2, 0)
        # hour_label = "2026-06-24 13" -> "06/24 13:00"
        try:
            parts = hl.split(" ")
            date_part = parts[0][5:]
            hour_part = parts[1]
            display = date_part.replace("-", "/") + " " + hour_part + ":00"
        except:
            display = str(hl)
        result.append({'hour': display, 'bonus_pool_out': round(bo_all, 2),
                       'permanent_bonus': round(permanent_bonus2, 2),
                       'bonus_withdraw': round(bo, 2),
                       'static_burn': round(sb, 2),
                       'dynamic_in': round(di, 2),
                       'dynamic_turbo': round(dynamic_release2, 2),
                       'dynamic_release': round(dynamic_release2, 2),
                       'transfer_720': round(t720, 2),
                       'stake_in': round(si2 + burn_stake2, 2),
                       'stake_pool_out': round(so_all, 2),
                       'permanent_stake': round(permanent_stake2, 2),
                       'stake_out': round(so2, 2)})
    TREND_CACHE.update({"ts": now_ts, "data": result})
    return [dict(row) for row in result]


def _to_float(value, default=0.0):
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _dex_pair_row(pair):
    txns = pair.get("txns") or {}
    volume = pair.get("volume") or {}
    price_change = pair.get("priceChange") or {}
    liquidity = pair.get("liquidity") or {}
    base = pair.get("baseToken") or {}
    quote = pair.get("quoteToken") or {}
    base_address = (base.get("address") or "").lower()
    quote_address = (quote.get("address") or "").lower()
    price_usd = _to_float(pair.get("priceUsd"))
    price_native = _to_float(pair.get("priceNative"))
    ark_price_usd = price_usd
    if quote_address == TOKEN_ARK and price_native:
        ark_price_usd = price_usd / price_native
    base_liquidity = _to_float(liquidity.get("base"))
    quote_liquidity = _to_float(liquidity.get("quote"))
    if not base_liquidity and base_address == TOKEN_ARK and ark_price_usd:
        base_liquidity = _to_float(liquidity.get("usd")) / ark_price_usd / 2
    if not quote_liquidity and quote_address == TOKEN_USDT:
        quote_liquidity = _to_float(liquidity.get("usd")) / 2
    periods = {}
    for key in ("m5", "h1", "h6", "h24"):
        period_txns = txns.get(key) or {}
        buys = int(period_txns.get("buys") or 0)
        sells = int(period_txns.get("sells") or 0)
        periods[key] = {
            "volume": _to_float(volume.get(key)),
            "txns": buys + sells,
            "buys": buys,
            "sells": sells,
            "price_change": _to_float(price_change.get(key)),
        }
    return {
        "chain_id": pair.get("chainId"),
        "dex_id": pair.get("dexId"),
        "pair_address": pair.get("pairAddress"),
        "url": pair.get("url"),
        "base_address": base_address,
        "quote_address": quote_address,
        "base_symbol": base.get("symbol") or "ARK",
        "quote_symbol": quote.get("symbol") or "",
        "price_usd": price_usd,
        "ark_price_usd": round(ark_price_usd, 10),
        "price_native": pair.get("priceNative"),
        "liquidity_usd": _to_float(liquidity.get("usd")),
        "base_liquidity": round(base_liquidity, 6),
        "quote_liquidity": round(quote_liquidity, 6),
        "ark_liquidity": round(base_liquidity if base_address == TOKEN_ARK else quote_liquidity, 6),
        "usdt_liquidity": round(base_liquidity if base_address == TOKEN_USDT else quote_liquidity, 6),
        "periods": periods,
        "volume_h24": periods["h24"]["volume"],
        "volume_h6": periods["h6"]["volume"],
        "volume_h1": periods["h1"]["volume"],
        "volume_m5": periods["m5"]["volume"],
        "txns_m5": periods["m5"]["txns"],
        "txns_h1": periods["h1"]["txns"],
        "txns_h6": periods["h6"]["txns"],
        "txns_h24": periods["h24"]["txns"],
        "buys_m5": periods["m5"]["buys"],
        "buys_h1": periods["h1"]["buys"],
        "buys_h6": periods["h6"]["buys"],
        "buys_h24": periods["h24"]["buys"],
        "sells_m5": periods["m5"]["sells"],
        "sells_h1": periods["h1"]["sells"],
        "sells_h6": periods["h6"]["sells"],
        "sells_h24": periods["h24"]["sells"],
        "price_change_m5": periods["m5"]["price_change"],
        "price_change_h24": periods["h24"]["price_change"],
        "price_change_h6": periods["h6"]["price_change"],
        "price_change_h1": periods["h1"]["price_change"],
        "fdv": _to_float(pair.get("fdv")),
        "market_cap": _to_float(pair.get("marketCap")),
        "pair_created_at": pair.get("pairCreatedAt"),
    }


def get_ark_dex_data():
    now = time.time()
    if DEX_CACHE["data"] and now - DEX_CACHE["ts"] < DEX_CACHE_TTL:
        data = dict(DEX_CACHE["data"])
        data["cached"] = True
        return data

    url = f"https://api.dexscreener.com/token-pairs/v1/bsc/{TOKEN_ARK}"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        raw_pairs = resp.json()
        if not isinstance(raw_pairs, list):
            raw_pairs = []
        pairs = [_dex_pair_row(pair) for pair in raw_pairs]
        pairs.sort(key=lambda p: p["liquidity_usd"], reverse=True)
        primary_pairs = [p for p in pairs if (p.get("pair_address") or "").lower() == DEX_PRIMARY_PAIR]
        visible_pairs = primary_pairs or [p for p in pairs if p["liquidity_usd"] >= DEX_MIN_LIQUIDITY_USD]
        if not visible_pairs and pairs:
            visible_pairs = [pairs[0]]
        best = visible_pairs[0] if visible_pairs else None
        period_totals = {}
        for key in ("m5", "h1", "h6", "h24"):
            period_totals[key] = {
                "volume": round(sum(p["periods"][key]["volume"] for p in visible_pairs), 2),
                "txns": sum(p["periods"][key]["txns"] for p in visible_pairs),
                "buys": sum(p["periods"][key]["buys"] for p in visible_pairs),
                "sells": sum(p["periods"][key]["sells"] for p in visible_pairs),
                "price_change": best["periods"][key]["price_change"] if best else 0,
            }
        data = {
            "updated_at": datetime.now(BJT).isoformat(),
            "source": "Dex Screener",
            "token_address": TOKEN_ARK,
            "pair_count": len(visible_pairs),
            "price_usd": best["ark_price_usd"] if best else 0,
            "price_native": best["price_native"] if best else "0",
            "price_change_m5": best["price_change_m5"] if best else 0,
            "price_change_h1": best["price_change_h1"] if best else 0,
            "price_change_h6": best["price_change_h6"] if best else 0,
            "price_change_h24": best["price_change_h24"] if best else 0,
            "periods": period_totals,
            "liquidity_usd": round(sum(p["liquidity_usd"] for p in visible_pairs), 2),
            "pool_ark": best["ark_liquidity"] if best else 0,
            "pool_usdt": best["usdt_liquidity"] if best else 0,
            "fdv": best["fdv"] if best else 0,
            "market_cap": best["market_cap"] if best else 0,
            "volume_h24": round(sum(p["volume_h24"] for p in visible_pairs), 2),
            "txns_h24": sum(p["txns_h24"] for p in visible_pairs),
            "buys_h24": sum(p["buys_h24"] for p in visible_pairs),
            "sells_h24": sum(p["sells_h24"] for p in visible_pairs),
            "pair_address": best["pair_address"] if best else DEX_PRIMARY_PAIR,
            "pair_created_at": best["pair_created_at"] if best else None,
            "best_pair": best,
            "pairs": visible_pairs[:12],
            "cached": False,
        }
        DEX_CACHE["ts"] = now
        DEX_CACHE["data"] = data
        return data
    except Exception as e:
        if DEX_CACHE["data"]:
            data = dict(DEX_CACHE["data"])
            data["cached"] = True
            data["stale"] = True
            data["error"] = str(e)
            return data
        return {
            "updated_at": datetime.now(BJT).isoformat(),
            "source": "Dex Screener",
            "token_address": TOKEN_ARK,
            "pair_count": 0,
            "price_usd": 0,
            "price_change_h24": 0,
            "liquidity_usd": 0,
            "volume_h24": 0,
            "txns_h24": 0,
            "best_pair": None,
            "pairs": [],
            "cached": False,
            "error": str(e),
        }


def _dex_snapshot_payload(date_str, dex_data):
    return {
        "price_usd": round(_to_float(dex_data.get("price_usd")), 6),
        "pool_ark": round(_to_float(dex_data.get("pool_ark")), 6),
        "pool_usdt": round(_to_float(dex_data.get("pool_usdt")), 6),
        "liquidity_usd": round(_to_float(dex_data.get("liquidity_usd")), 2),
        "pair_address": dex_data.get("pair_address") or DEX_PRIMARY_PAIR,
        "source_updated_at": dex_data.get("updated_at"),
        "snapshot_at": datetime.now(BJT).isoformat(),
    }


def capture_dex_daily_snapshot(date_str=None, force=False):
    now = datetime.now(BJT)
    target_date = date_str or (now - timedelta(days=1)).strftime("%Y-%m-%d")
    with DEX_SNAPSHOT_LOCK:
        if not force and get_dex_daily_snapshot(target_date):
            return get_dex_daily_snapshot(target_date)
        dex_data = get_ark_dex_data()
        if not dex_data or dex_data.get("error"):
            return None
        upsert_dex_daily_snapshot(target_date, **_dex_snapshot_payload(target_date, dex_data))
        return get_dex_daily_snapshot(target_date)


def ensure_latest_dex_daily_snapshot():
    now = datetime.now(BJT)
    if not (now.hour == 0 and now.minute <= 5):
        return None
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    if not get_dex_daily_snapshot(yesterday):
        return capture_dex_daily_snapshot(yesterday)
    return None


def dex_daily_snapshot_worker():
    while True:
        try:
            now = datetime.now(BJT)
            if now.hour == 0 and now.minute <= 5:
                ensure_latest_dex_daily_snapshot()
            time.sleep(60)
        except Exception as e:
            print(f"[Dex Snapshot] 异常: {e}")
            time.sleep(60)


# ---------- Telegram /today 命令轮询 ----------
def _attach_realtime_dex_data(record):
    try:
        dex_data = get_ark_dex_data()
        if dex_data and not dex_data.get("error"):
            record["pool_ark"] = round(_to_float(dex_data.get("pool_ark")), 2)
            record["pool_usdt"] = round(_to_float(dex_data.get("pool_usdt")), 2)
            record["ark_price"] = round(_to_float(dex_data.get("price_usd")), 6)
            prev_date = (datetime.strptime(record["date"], "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            prev_snapshot = get_dex_daily_snapshot(prev_date)
            if prev_snapshot:
                record["pool_ark_delta"] = round(
                    record["pool_ark"] - _to_float(prev_snapshot.get("pool_ark")), 2
                )
                record["pool_usdt_delta"] = round(
                    record["pool_usdt"] - _to_float(prev_snapshot.get("pool_usdt")), 2
                )
                record["ark_price_delta"] = round(
                    record["ark_price"] - _to_float(prev_snapshot.get("price_usd")), 6
                )
    except Exception as e:
        print(f"[Telegram Poll] Dex 数据获取失败: {e}")

    conn = get_conn()
    row = conn.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN side='buy_ark' THEN amount_usdt ELSE 0 END),0) as buy_value_usdt,
            COALESCE(SUM(CASE WHEN side='sell_ark' THEN amount_usdt ELSE 0 END),0) as sell_value_usdt
        FROM lp_swaps
        WHERE timestamp >= ? AND timestamp < ?
    """, (
        record["date"] + " 00:00:00",
        (datetime.strptime(record["date"], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d 00:00:00"),
    )).fetchone()
    conn.close()
    record["buy_value_usdt"] = round(float(row["buy_value_usdt"]) if row else 0, 2)
    record["sell_value_usdt"] = round(float(row["sell_value_usdt"]) if row else 0, 2)
    return record

def _send_today(record, chat_id=None):
    """只推 Telegram，不写飞书"""
    push_to_telegram(
        _attach_realtime_dex_data(record),
        target_chat_id=chat_id,
        title_suffix="实时数据",
    )

def _send_chat_id(chat_id, title=""):
    text = f"当前群 ID：{chat_id}"
    if title:
        text += f"\n群名：{title}"
    requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
        json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
        timeout=15,
    )

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
                chat = msg.get("chat", {})
                chat_id = chat.get("id")
                text = msg.get("text", "").strip()
                command = text.split()[0].split("@")[0].lower() if text else ""
                if command == "/id":
                    print(f"[Telegram Poll] 收到 /id 命令 chat_id={chat_id}")
                    _send_chat_id(chat_id, chat.get("title", ""))
                    continue
                # 只响应来自已配置推送群组的 /today 命令
                if chat_id in get_telegram_chat_ids() and command == "/today":
                    print(f"[Telegram Poll] 收到 /today 命令")
                    record = get_today_data()
                    _send_today(record, chat_id=chat_id)
        except Exception as e:
            print(f"[Telegram Poll] 异常: {e}")
            time.sleep(5)

# 启动后台轮询
threading.Thread(target=telegram_poll, daemon=True).start()
threading.Thread(target=dex_daily_snapshot_worker, daemon=True).start()

@app.get("/api/today")
def get_today():
    return {"data": get_today_data()}


@app.get("/api/today-trend")
def get_today_trend_api():
    return {"data": get_today_trend()}

@app.get("/api/dex/ark")
def get_ark_dex():
    return {"data": get_ark_dex_data()}

@app.get("/api/dex/pool-history")
def get_dex_pool_history(page:int=1, per_page:int=20):
    ensure_latest_dex_daily_snapshot()
    page = max(1, int(page or 1))
    per_page = min(20, max(1, int(per_page or 20)))
    offset = (page - 1) * per_page
    rows, total = get_dex_daily_snapshots(per_page + 1, offset)
    next_row = rows[per_page] if len(rows) > per_page else None
    return {
        "data": rows[:per_page],
        "next_row": next_row,
        "total": total,
        "page": page,
        "per_page": per_page,
    }

@app.get("/api/dex/capture-pool")
def capture_dex_pool(date: str = None):
    target_date = date or datetime.now(BJT).strftime("%Y-%m-%d")
    row = capture_dex_daily_snapshot(target_date, force=True)
    return {"status": "ok" if row else "error", "data": row}

@app.get("/api/daily")
def get_daily(page: int = 1, per_page: int = 10):
    conn = get_conn()
    today = datetime.now(BJT).strftime("%Y-%m-%d")
    # 先查总数
    total_row = conn.execute("SELECT COUNT(*) FROM daily_summary WHERE date < ?", (today,)).fetchone()
    total = total_row[0] if total_row else 0
    offset = (page - 1) * per_page
    rows = conn.execute("SELECT * FROM daily_summary WHERE date < ? ORDER BY date DESC LIMIT ? OFFSET ?", (today, per_page, offset)).fetchall()
    result = [dict(r) for r in rows]
    conn.close()
    return {"data": result, "count": len(result), "total": total, "page": page, "per_page": per_page}



@app.get("/api/realtime")
def get_realtime(limit:int=100):
    conn = get_conn()
    # dynamic 是旧版按 ARK 转账写入的辅助记录，static_burn 是识别静态释放的 gARK 销毁凭据。
    # 两者都已有对应业务事件，事件流不再展示，避免同一笔交易重复出现。
    rows = conn.execute("""
        SELECT * FROM events
        WHERE type NOT IN ('dynamic', 'static_burn')
        ORDER BY timestamp DESC, block DESC, id DESC
        LIMIT ?
    """, (limit,)).fetchall()
    conn.close()
    return {"data": [dict(r) for r in rows]}

@app.get("/api/lp-swaps")
def get_lp_swaps(limit:int=100, period: str = "h24"):
    period_hours = {"m5": 5 / 60, "h1": 1, "h6": 6, "h24": 24}
    hours = period_hours.get(period, 24)
    cutoff = (datetime.now(BJT) - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")
    conn = get_conn()
    rows = conn.execute("SELECT * FROM lp_swaps ORDER BY block DESC, log_index DESC LIMIT ?", (limit,)).fetchall()
    summary = conn.execute("""
        SELECT
            COUNT(*) as count,
            MAX(block) as last_block,
            COALESCE(SUM(CASE WHEN side='buy_ark' THEN amount_ark ELSE 0 END),0) as buy_ark,
            COALESCE(SUM(CASE WHEN side='sell_ark' THEN amount_ark ELSE 0 END),0) as sell_ark,
            COALESCE(SUM(CASE WHEN side='buy_ark' THEN amount_usdt ELSE 0 END),0) as buy_value_usdt,
            COALESCE(SUM(CASE WHEN side='sell_ark' THEN amount_usdt ELSE 0 END),0) as sell_value_usdt,
            COALESCE(SUM(amount_usdt),0) as volume_usdt
        FROM lp_swaps
        WHERE timestamp >= ?
    """, (cutoff,)).fetchone()
    conn.close()
    result = dict(summary) if summary else {}
    result["period"] = period
    result["cutoff"] = cutoff
    return {"data": [dict(r) for r in rows], "summary": result}

@app.get("/api/force-summary")
def force_summary():
    record = _attach_realtime_dex_data(get_today_data())
    push_to_feishu(record)
    push_to_telegram(record)
    return {"status":"ok","data":record}

@app.get("/health")
def health():
    return {"status":"ok","time":datetime.now(BJT).isoformat()}

@app.get("/")
def index():
    return FileResponse(os.path.join(BASE_DIR, "dashboard.html"))
