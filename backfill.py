#!/usr/bin/env python3
"""容器启动时补齐今日缺失数据（小批量模式，每10块一次eth_getLogs）"""
import sqlite3, time, requests, sys
from datetime import datetime, timezone, timedelta

BJT = timezone(timedelta(hours=8))

RPC_URLS = [
    "https://bsc-mainnet.nodereal.io/v1/70208501917a413bab46cb281fc0997f",
    "https://bsc-mainnet.nodereal.io/v1/1ad9525366ba4b56a0a2b4fef2b2fef7",
    "https://rpc.ankr.com/bsc/c9251b3e097417a6e558de2dce53c2d276a591fbd89f2ec9f017392936a5e0b5",
    "https://bsc.mytokenpocket.vip",
]

class RPCManager:
    def __init__(self, urls):
        self.urls = urls
        self.index = 0

    def call(self, method, params, retries=3):
        for _ in range(len(self.urls)):
            url = self.urls[self.index]
            for _ in range(retries):
                try:
                    r = requests.post(url, json={"jsonrpc":"2.0","method":method,"params":params,"id":1}, timeout=20)
                    d = r.json()
                    if "error" in d:
                        time.sleep(1)
                        continue
                    return d["result"]
                except:
                    time.sleep(1)
                    continue
            self.index = (self.index + 1) % len(self.urls)
        raise Exception("RPC 均不可用")

RPC = RPCManager(RPC_URLS)

TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
TOKEN_ARK = "0xCae117ca6Bc8A341D2E7207F30E180f0e5618B9D".lower()
TOKEN_GARK = "0x911f12D137D74E5917877f87cf8A8bB2FDde557f".lower()
DECIMALS = 18
BONUS_POOL = "0x8501168656FcaC4628F6910CcABEA8B64Ebe5BD4".lower()
STAKE_POOL = "0xd1D95292F450b665566df4c4255615eF4Ed9BD0B".lower()
TARGET_DYNAMIC = "0x8366a748E02F730911Cb5AB4fd049d2E1e0414b7".lower()
BURN_ADDR = "0x0000000000000000000000000000000000000000"
BURN_ADDR2 = "0x000000000000000000000000000000000000dead"
REF_BLOCK = 105553753
BASE_TS = 1782057600.0
BLOCK_SEC = 0.45
BATCH_SIZE = 20

def process_batch(from_block, to_block):
    results = []
    try:
        ark_logs = RPC.call("eth_getLogs", [{
            "fromBlock": hex(from_block), "toBlock": hex(to_block),
            "address": TOKEN_ARK, "topics": [TRANSFER_TOPIC]
        }])
        if ark_logs:
            for log in ark_logs:
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
                results.append((bn, log.get("transactionHash",""), etype, fr, to, val, ts))

        gark_logs = RPC.call("eth_getLogs", [{
            "fromBlock": hex(from_block), "toBlock": hex(to_block),
            "address": TOKEN_GARK, "topics": [TRANSFER_TOPIC]
        }])
        if gark_logs:
            for log in gark_logs:
                to = "0x" + log["topics"][2][26:]
                if to in (BURN_ADDR, BURN_ADDR2):
                    bn = int(log["blockNumber"], 16)
                    fr = "0x" + log["topics"][1][26:]
                    val = int(log["data"], 16) / 10**DECIMALS
                    ts = datetime.fromtimestamp(BASE_TS + (bn - REF_BLOCK) * BLOCK_SEC, BJT).isoformat()
                    results.append((bn, log.get("transactionHash",""), "static_burn", fr, to, val, ts))
    except:
        pass
    return results

def backfill_today():
    try:
        cur = int(RPC.call("eth_blockNumber", []), 16)
    except:
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

    print(f"[补齐] 补充 #{start_block} -> #{cur} (每{BATCH_SIZE}块一批)")
    total = 0
    conn2 = sqlite3.connect("/app/data/ark_monitor.db")
    for s in range(start_block, cur + 1, BATCH_SIZE):
        e = min(s + BATCH_SIZE - 1, cur)
        batch = process_batch(s, e)
        if batch:
            conn2.executemany(
                "INSERT OR IGNORE INTO events (block, tx, type, from_addr, to_addr, value, timestamp) VALUES (?,?,?,?,?,?,?)",
                batch
            )
            conn2.commit()
            total += len(batch)
        if (s - start_block) % 500 == 0:
            print(f"  [补齐] #{s} 已补 {total} 条")
    conn2.close()
    print(f"[补齐] 完成！补充 {total} 条事件")

if __name__ == "__main__":
    backfill_today()
