#!/usr/bin/env python3
"""容器启动时补齐今日缺失数据"""
import sqlite3, time, requests, sys
from datetime import datetime, timezone, timedelta

BJT = timezone(timedelta(hours=8))
REF_BLOCK = 105553753
BASE_TS = 1782057600.0
BLOCK_SEC = 0.45
DECIMALS = 18
TOKEN_ARK = "0xCae117ca6Bc8A341D2E7207F30E180f0e5618B9D".lower()
TOKEN_GARK = "0x911f12D137D74E5917877f87cf8A8bB2FDde557f".lower()
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
BONUS_POOL = "0x8501168656FcaC4628F6910CcABEA8B64Ebe5BD4".lower()
STAKE_POOL = "0xd1D95292F450b665566df4c4255615eF4Ed9BD0B".lower()
TARGET_DYNAMIC = "0x8366a748E02F730911Cb5AB4fd049d2E1e0414b7".lower()
BURN_ADDR = "0x0000000000000000000000000000000000000000"
BURN_ADDR2 = "0x000000000000000000000000000000000000dead"
RPC_URLS = [
    "https://bsc-mainnet.nodereal.io/v1/70208501917a413bab46cb281fc0997f",
    "https://bsc.mytokenpocket.vip",
]

def _rpc(url, method, params, retries=3):
    for i in range(retries):
        try:
            r = requests.post(url, json={"jsonrpc":"2.0","method":method,"params":params,"id":1}, timeout=20)
            d = r.json()
            if "error" in d:
                if i < retries-1: time.sleep(1); continue
                raise Exception(d["error"]["message"])
            return d["result"]
        except:
            if i < retries-1: time.sleep(1); continue
    return None

def backfill_today():
    today = datetime.now(BJT).strftime("%Y-%m-%d")

    cur = None
    for url in RPC_URLS:
        try:
            r = _rpc(url, "eth_blockNumber", [])
            if r: cur = int(r, 16); break
        except: continue
    if not cur:
        print("[补齐] 无法获取最新区块")
        return

    conn = sqlite3.connect("/app/data/ark_monitor.db")
    db_max = conn.execute("SELECT MAX(block) FROM events").fetchone()[0]
    conn.close()

    start_block = REF_BLOCK
    if db_max and db_max >= start_block:
        start_block = db_max + 1

    if start_block > cur:
        print(f"[补齐] 无需补充 (db_max={db_max}, latest={cur})")
        return

    print(f"[补齐] 补充 #{start_block} -> #{cur}")

    CHUNK = 5000
    total = 0
    for s in range(start_block, cur + 1, CHUNK):
        e = min(s + CHUNK - 1, cur)
        ark = _rpc(RPC_URLS[0], "eth_getLogs", [{
            "fromBlock": hex(s), "toBlock": hex(e),
            "address": TOKEN_ARK, "topics": [TRANSFER_TOPIC]
        }], retries=3)
        if ark is None:
            ark = _rpc(RPC_URLS[1], "eth_getLogs", [{
                "fromBlock": hex(s), "toBlock": hex(e),
                "address": TOKEN_ARK, "topics": [TRANSFER_TOPIC]
            }], retries=3)
        if ark is None: ark = []

        gark = _rpc(RPC_URLS[0], "eth_getLogs", [{
            "fromBlock": hex(s), "toBlock": hex(e),
            "address": TOKEN_GARK, "topics": [TRANSFER_TOPIC]
        }], retries=3)
        if gark is None:
            gark = _rpc(RPC_URLS[1], "eth_getLogs", [{
                "fromBlock": hex(s), "toBlock": hex(e),
                "address": TOKEN_GARK, "topics": [TRANSFER_TOPIC]
            }], retries=3)
        if gark is None: gark = []

        batch = []
        for log in ark:
            bn = int(log["blockNumber"], 16)
            fr = "0x" + log["topics"][1][26:]
            to = "0x" + log["topics"][2][26:]
            val = int(log["data"], 16) / 10**DECIMALS
            ts = datetime.fromtimestamp(BASE_TS + (bn - REF_BLOCK) * BLOCK_SEC, BJT).isoformat()
            if fr == BONUS_POOL: etype = "bonus_withdraw"
            elif to == STAKE_POOL: etype = "stake_in"
            elif fr == STAKE_POOL: etype = "stake_out"
            elif to == TARGET_DYNAMIC: etype = "dynamic"
            else: continue
            batch.append((bn, log.get("transactionHash",""), etype, fr, to, val, ts))

        for log in gark:
            to = "0x" + log["topics"][2][26:]
            if to in (BURN_ADDR, BURN_ADDR2):
                bn = int(log["blockNumber"], 16)
                fr = "0x" + log["topics"][1][26:]
                val = int(log["data"], 16) / 10**DECIMALS
                ts = datetime.fromtimestamp(BASE_TS + (bn - REF_BLOCK) * BLOCK_SEC, BJT).isoformat()
                batch.append((bn, log.get("transactionHash",""), "static_burn", fr, to, val, ts))

        if batch:
            conn2 = sqlite3.connect("/app/data/ark_monitor.db")
            conn2.executemany(
                "INSERT OR IGNORE INTO events (block, tx, type, from_addr, to_addr, value, timestamp) VALUES (?,?,?,?,?,?,?)",
                batch
            )
            conn2.commit()
            conn2.close()
            total += len(batch)

        time.sleep(0.3)

    print(f"[补齐] 完成！补充 {total} 条事件")

if __name__ == "__main__":
    backfill_today()
