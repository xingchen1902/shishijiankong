#!/usr/bin/env python3
"""补齐 transfer_720 漏块事件"""
import os, sys, time, sqlite3, requests
from datetime import datetime, timezone, timedelta

BJT = timezone(timedelta(hours=8))
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

RPC_URLS = [
    "https://bsc-mainnet.nodereal.io/v1/70208501917a413bab46cb281fc0997f",
    "https://bsc-mainnet.nodereal.io/v1/1ad9525366ba4b56a0a2b4fef2b2fef7",
    "https://rpc.ankr.com/bsc/c9251b3e097417a6e558de2dce53c2d276a591fbd89f2ec9f017392936a5e0b5",
    "https://bsc.mytokenpocket.vip",
]

TOKEN_ARK = "0xCae117ca6Bc8A341D2E7207F30E180f0e5618B9D".lower()
DECIMALS = 18
BONUS_POOL = "0x8501168656FcaC4628F6910CcABEA8B64Ebe5BD4".lower()
STAKE_POOL = "0xd1D95292F450b665566df4c4255615eF4Ed9BD0B".lower()
REF_BLOCK = 105553753
BASE_TS = 1782057600.0
BLOCK_SEC = 0.45

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
                        err = d.get("error", {}).get("message", "")
                        if any(k in err.lower() for k in ["exceed", "limit", "quota", "429", "rate", "too many"]):
                            break
                        time.sleep(1)
                        continue
                    return d["result"]
                except:
                    time.sleep(1)
                    continue
            self.index = (self.index + 1) % len(self.urls)
        raise Exception("RPC all failed")

_rpc = RPCManager(RPC_URLS)
DB_PATH = "/app/data/ark_monitor.db"

def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def get_existing_tx_set(conn):
    rows = conn.execute("SELECT DISTINCT tx FROM events WHERE type='transfer_720'").fetchall()
    return set(r["tx"] for r in rows)

def backfill(from_block, to_block, existing_txs):
    total_new = 0
    step = 200
    scanned = 0
    for start in range(from_block, to_block + 1, step):
        end = min(start + step - 1, to_block)
        try:
            logs = _rpc.call("eth_getLogs", [{
                "fromBlock": hex(start),
                "toBlock": hex(end),
                "address": TOKEN_ARK,
                "topics": [TRANSFER_TOPIC]
            }])
        except Exception as e:
            print(f"  [FAIL] #{start}~#{end}: {e}")
            time.sleep(2)
            continue
        scanned += end - start + 1
        if not logs:
            continue
        events = []
        for log in logs:
            fr = "0x" + log["topics"][1][26:]
            to = "0x" + log["topics"][2][26:]
            if fr == BONUS_POOL and to == STAKE_POOL:
                tx = log.get("transactionHash", "")
                if tx in existing_txs:
                    continue
                bn = int(log["blockNumber"], 16)
                val = int(log["data"], 16) / 10**DECIMALS
                ts = datetime.fromtimestamp(BASE_TS + (bn - REF_BLOCK) * BLOCK_SEC, BJT).isoformat()
                events.append((bn, tx, "transfer_720", fr, to, val, ts))
                existing_txs.add(tx)
        if events:
            conn = get_conn()
            conn.executemany(
                "INSERT INTO events (block, tx, type, from_addr, to_addr, value, timestamp) VALUES (?,?,?,?,?,?,?)",
                events
            )
            conn.commit()
            conn.close()
            total_new += len(events)
            print(f"  [OK] #{start}~#{end} +{len(events)} (total {total_new})")
        time.sleep(0.15)
    return total_new, scanned

def main():
    global DB_PATH
    if not os.path.exists(DB_PATH) and os.path.exists("data/ark_monitor.db"):
        DB_PATH = "data/ark_monitor.db"

    start_block = int(sys.argv[1]) if len(sys.argv) > 1 else 106018641
    try:
        latest = int(_rpc.call("eth_blockNumber", []), 16)
        safe = latest - 1
    except:
        print("Failed to get current block")
        return

    print(f"Start: {start_block}")
    print(f"End: {safe}")
    print(f"Blocks: {safe - start_block + 1}")

    conn = get_conn()
    existing = get_existing_tx_set(conn)
    conn.close()
    print(f"Existing transfer_720: {len(existing)}")

    print("\nBackfilling...")
    t0 = time.time()
    new_count, scanned = backfill(start_block, safe, existing)
    elapsed = time.time() - t0
    print(f"Done: +{new_count} events, {scanned} blocks, {elapsed:.0f}s")

if __name__ == "__main__":
    main()
