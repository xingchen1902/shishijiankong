#!/usr/bin/env python3
"""补齐所有类型事件（从指定区块到最新）"""
import os, sys, time, sqlite3, requests
from datetime import datetime, timezone, timedelta

BJT = timezone(timedelta(hours=8))
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

RPC_URLS = [
    "https://bsc-mainnet.nodereal.io/v1/70208501917a413bab46cb281fc0997f",
]

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
    """获取已存在的所有事件 tx（按类型去重）"""
    types = ["bonus_withdraw", "stake_in", "stake_out", "static_burn", "dynamic", "transfer_720"]
    result = {}
    for t in types:
        rows = conn.execute("SELECT DISTINCT tx FROM events WHERE type=?", (t,)).fetchall()
        result[t] = set(r["tx"] for r in rows)
    # 也建立一个全局 tx 集合用于快速判断
    rows = conn.execute("SELECT tx, type FROM events").fetchall()
    all_tx = {}
    for r in rows:
        if r["tx"] not in all_tx:
            all_tx[r["tx"]] = []
        all_tx[r["tx"]].append(r["type"])
    return result, all_tx

def classify_ark_log(log):
    """解析单条 ARK log 的事件类型"""
    fr = "0x" + log["topics"][1][26:]
    to = "0x" + log["topics"][2][26:]
    bn = int(log["blockNumber"], 16)
    tx = log.get("transactionHash", "")
    val = int(log["data"], 16) / 10**DECIMALS
    ts = datetime.fromtimestamp(BASE_TS + (bn - REF_BLOCK) * BLOCK_SEC, BJT).isoformat()

    if fr == BONUS_POOL and to == STAKE_POOL:
        etype = "transfer_720"
    elif fr == BONUS_POOL:
        etype = "bonus_withdraw"
    elif to == STAKE_POOL:
        etype = "stake_in"
    elif fr == STAKE_POOL:
        etype = "stake_out"
    elif to == TARGET_DYNAMIC:
        etype = "dynamic"
    else:
        return None

    return {"block": bn, "tx": tx, "type": etype, "from": fr, "to": to, "value": val, "timestamp": ts}

def backfill_all(from_block, to_block, existing_by_type, all_txs):
    total_new = {t: 0 for t in ["bonus_withdraw", "stake_in", "stake_out", "static_burn", "dynamic", "transfer_720"]}
    step = 200
    scanned = 0

    for start in range(from_block, to_block + 1, step):
        end = min(start + step - 1, to_block)
        scanned += end - start + 1

        # 1. ARK logs
        events = []
        try:
            ark_logs = _rpc.call("eth_getLogs", [{
                "fromBlock": hex(start), "toBlock": hex(end),
                "address": TOKEN_ARK, "topics": [TRANSFER_TOPIC]
            }])
        except:
            time.sleep(2)
            continue

        if ark_logs:
            for log in ark_logs:
                tx = log.get("transactionHash", "")
                # 跳过已存在的事件（按类型）
                ev = classify_ark_log(log)
                if not ev:
                    continue
                if tx in all_txs:
                    if ev["type"] in all_txs[tx]:
                        continue
                events.append(ev)
                if tx not in all_txs:
                    all_txs[tx] = []
                all_txs[tx].append(ev["type"])

        # 2. gARK logs (static_burn)
        try:
            gark_logs = _rpc.call("eth_getLogs", [{
                "fromBlock": hex(start), "toBlock": hex(end),
                "address": TOKEN_GARK, "topics": [TRANSFER_TOPIC]
            }])
        except:
            gark_logs = None

        if gark_logs:
            for log in gark_logs:
                to = "0x" + log["topics"][2][26:]
                if to in (BURN_ADDR, BURN_ADDR2):
                    tx = log.get("transactionHash", "")
                    if tx in all_txs and "static_burn" in all_txs[tx]:
                        continue
                    bn = int(log["blockNumber"], 16)
                    fr = "0x" + log["topics"][1][26:]
                    val = int(log["data"], 16) / 10**DECIMALS
                    ts = datetime.fromtimestamp(BASE_TS + (bn - REF_BLOCK) * BLOCK_SEC, BJT).isoformat()
                    events.append({"block": bn, "tx": tx, "type": "static_burn",
                                   "from": fr, "to": to, "value": val, "timestamp": ts})
                    if tx not in all_txs:
                        all_txs[tx] = []
                    all_txs[tx].append("static_burn")

        # 写入
        if events:
            conn = get_conn()
            data = [(e["block"], e["tx"], e["type"], e["from"], e["to"], e["value"], e["timestamp"]) for e in events]
            conn.executemany(
                "INSERT INTO events (block, tx, type, from_addr, to_addr, value, timestamp) VALUES (?,?,?,?,?,?,?)",
                data
            )
            conn.commit()
            conn.close()
            for e in events:
                total_new[e["type"]] = total_new.get(e["type"], 0) + 1
            summary = ", ".join(f"{k}=+{v}" for k, v in total_new.items() if v > 0)
            if scanned % 2000 == 0:
                print(f"  [扫] {scanned} 块, {summary}")

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
    existing_by_type, all_txs = get_existing_tx_set(conn)
    conn.close()
    total_existing = sum(len(v) for v in existing_by_type.values())
    print(f"Existing events: {total_existing}")

    print("\nBackfilling...")
    t0 = time.time()
    results, scanned = backfill_all(start_block, safe, existing_by_type, all_txs)
    elapsed = time.time() - t0

    print(f"\nDone: {scanned} blocks in {elapsed:.0f}s")
    for t, c in results.items():
        if c > 0:
            print(f"  {t}: +{c}")

if __name__ == "__main__":
    main()
