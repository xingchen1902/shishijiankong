#!/usr/bin/env python3
"""
ARK 事件解析器
- 从区块交易中提取 ARK Transfer 事件
- 按地址分类：奖金池提取、质押/赎回、涡轮
- 存入 SQLite
"""

import json, time
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
                if i<retries-1: time.sleep(1); continue
                raise Exception(d["error"]["message"])
            return d["result"]
        except: continue
    return None

def get_logs(from_b, to_b, address, topics):
    for url in RPC_URLS:
        try:
            return _rpc(url, "eth_getLogs", [{
                "fromBlock": hex(from_b), "toBlock": hex(to_b),
                "address": address, "topics": topics,
            }])
        except: continue
    return []

def get_balance(token, address, block_hex="latest"):
    data = "0x70a08231" + address[2:].lower().zfill(64)
    for url in RPC_URLS:
        try:
            r = _rpc(url, "eth_call", [{"to": token, "data": data}, block_hex])
            if r: return int(r, 16)
        except: continue
    return 0

class EventParser:
    """解析单笔 ARK Transfer 事件"""

    def __init__(self):
        self.events = []  # 累计事件列表

    def parse_transaction(self, tx, block_num, block_ts):
        """解析一笔交易中的 Transfer 事件"""
        token = tx.get("to", "").lower()
        if token != TOKEN_ARK and token != TOKEN_GARK:
            return None

        if not tx.get("input") or tx["input"] == "0x":
            return None

        # 直接调 balanceOf 查询对端，这里简化处理
        # 实际完整解析需要 eth_getTransactionReceipt 拿 logs
        return None

    def parse_block_logs(self, block_num, block_ts):
        """从区块提取所有 ARK Transfer logs（使用 eth_getLogs 按地址索引）"""
        results = []
        pad_bonus = "0x" + "0"*24 + BONUS_POOL[2:]
        pad_stake = "0x" + "0"*24 + STAKE_POOL[2:]
        pad_target = "0x" + "0"*24 + TARGET_DYNAMIC[2:]
        pad_burn0 = "0x" + "0"*24 + BURN_ADDR[2:]
        pad_burn1 = "0x" + "0"*24 + BURN_ADDR2[2:]

        def classify(logs, event_type, direction):
            for l in logs:
                val = int(l["data"], 16) / 10**DECIMALS
                tx_hash = l.get("transactionHash", "")
                fr = "0x" + l["topics"][1][26:]
                to = "0x" + l["topics"][2][26:]
                results.append({
                    "block": block_num,
                    "tx": tx_hash,
                    "type": event_type,
                    "from": fr,
                    "to": to,
                    "value": val,
                    "timestamp": block_ts.isoformat() if hasattr(block_ts, 'isoformat') else str(block_ts),
                })

        # 奖金池提取（from=bonus_pool）
        classify(get_logs(block_num, block_num, TOKEN_ARK, [TRANSFER_TOPIC, pad_bonus]),
                 "bonus_withdraw", "out")
        # 质押转入（to=stake_pool）
        classify(get_logs(block_num, block_num, TOKEN_ARK, [TRANSFER_TOPIC, None, pad_stake]),
                 "stake_in", "in")
        # 赎回（from=stake_pool）
        classify(get_logs(block_num, block_num, TOKEN_ARK, [TRANSFER_TOPIC, pad_stake]),
                 "stake_out", "out")
        # 动静态涡轮（to=target）
        classify(get_logs(block_num, block_num, TOKEN_ARK, [TRANSFER_TOPIC, None, pad_target]),
                 "dynamic", "in")
        # gARK 销毁
        for pad in [pad_burn0, pad_burn1]:
            classify(get_logs(block_num, block_num, TOKEN_GARK, [TRANSFER_TOPIC, None, pad]),
                     "static_burn", "burn")

        self.events.extend(results)
        return results

    def clear(self):
        self.events = []
