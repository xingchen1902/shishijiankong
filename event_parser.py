#!/usr/bin/env python3
"""
ARK 事件解析器
- eth_getLogs 查小批量（每 10 块一批，省 CU 模式）
- 按地址分类：奖金池提取、质押/赎回、涡轮
- 余额缓存，不高频调 eth_call
"""

import time, json
from datetime import datetime, timezone, timedelta
import requests

BJT = timezone(timedelta(hours=8))
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"

TOKEN_ARK = "0xCae117ca6Bc8A341D2E7207F30E180f0e5618B9D".lower()
TOKEN_GARK = "0x911f12D137D74E5917877f87cf8A8bB2FDde557f".lower()
DECIMALS = 18

BONUS_POOL = "0x8501168656FcaC4628F6910CcABEA8B64Ebe5BD4".lower()
STAKE_POOL = "0xd1D95292F450b665566df4c4255615eF4Ed9BD0B".lower()
TARGET_DYNAMIC = "0x8366a748E02F730911Cb5AB4fd049d2E1e0414b7".lower()
BURN_ADDR = "0x0000000000000000000000000000000000000000"
BURN_ADDR2 = "0x000000000000000000000000000000000000dead"

BATCH_SIZE = 10
# 基准：105553753 (BJT 2026-06-22 00:00:01)
REF_BLOCK = 105553753
BASE_TS = 1782057600.0
BLOCK_SEC = 0.45

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

_rpc = RPCManager(RPC_URLS)

def _rpc_call(method, params, retries=3):
    try:
        return _rpc.call(method, params, retries)
    except:
        return None

def get_balance(token, address, block_hex="latest"):
    """eth_call 查余额（仅在汇总时需要，不高频调用）"""
    data = "0x70a08231" + address[2:].lower().zfill(64)
    r = _rpc_call("eth_call", [{"to": token, "data": data}, block_hex])
    return int(r, 16) if r else 0

def _classify_logs(logs, from_block, to_block):
    """解析 ARK logs，按地址分类"""
    results = []
    for log in logs:
        bn = int(log["blockNumber"], 16)
        tx = log.get("transactionHash", "")
        fr = "0x" + log["topics"][1][26:]
        to = "0x" + log["topics"][2][26:]
        val = int(log["data"], 16) / 10**DECIMALS
        ts = datetime.fromtimestamp(BASE_TS + (bn - REF_BLOCK) * BLOCK_SEC, BJT).isoformat()

        if fr == BONUS_POOL:
            etype = "bonus_withdraw"
        elif to == STAKE_POOL:
            etype = "stake_in"
        elif fr == STAKE_POOL:
            etype = "stake_out"
        elif to == TARGET_DYNAMIC:
            etype = "dynamic"
        else:
            continue

        results.append({
            "block": bn, "tx": tx, "type": etype,
            "from": fr, "to": to, "value": val, "timestamp": ts,
        })
    return results


class EventParser:
    """解析批量 ARK Transfer 事件（每批调 2 次 eth_getLogs）"""

    def __init__(self):
        self.events = []
        self._balance_cache = {}

    def process_batch(self, from_block, to_block):
        """批量 eth_getLogs，提取 ARK/gARK Transfer 事件"""
        results = []

        # 1. ARK 批量 getLogs
        ark_logs = _rpc_call("eth_getLogs", [{
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
            "address": TOKEN_ARK,
            "topics": [TRANSFER_TOPIC]
        }])
        if ark_logs:
            results.extend(_classify_logs(ark_logs, from_block, to_block))

        # 2. gARK 批量 getLogs（查销毁）
        gark_logs = _rpc_call("eth_getLogs", [{
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
            "address": TOKEN_GARK,
            "topics": [TRANSFER_TOPIC]
        }])
        if gark_logs:
            for log in gark_logs:
                to = "0x" + log["topics"][2][26:]
                if to in (BURN_ADDR, BURN_ADDR2):
                    bn = int(log["blockNumber"], 16)
                    tx = log.get("transactionHash", "")
                    fr = "0x" + log["topics"][1][26:]
                    val = int(log["data"], 16) / 10**DECIMALS
                    ts = datetime.fromtimestamp(BASE_TS + (bn - REF_BLOCK) * BLOCK_SEC, BJT).isoformat()
                    results.append({
                        "block": bn, "tx": tx, "type": "static_burn",
                        "from": fr, "to": to, "value": val, "timestamp": ts,
                    })

        self.events.extend(results)
        if results:
            for e in results[:3]:
                print(f"  [{e['type']}] {e['value']:.4f}  #{e['block']}")
            print(f"  [批量] #{from_block}~#{to_block} 共 {len(results)} 条")
        return results

    def clear(self):
        self.events = []

    def get_cached_balance(self, token, address):
        key = f"{token}_{address}"
        now = time.time()
        if key in self._balance_cache:
            val, ts = self._balance_cache[key]
            if now - ts < 60:
                return val
        bal = get_balance(token, address)
        self._balance_cache[key] = (bal, now)
        return bal
