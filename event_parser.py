#!/usr/bin/env python3
"""
ARK 事件解析器
- eth_getLogs 查批量（每 200 块一批，省 CU 模式）
- 按地址分类：奖金池提取、质押/赎回、涡轮
- 余额缓存，不高频调 eth_call
"""

import time, json
from datetime import datetime, timezone, timedelta
import requests

BJT = timezone(timedelta(hours=8))
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
SWAP_TOPIC = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
RELEASE_TOPIC = "0x3b528916c884f3594beeba6799acd20b08bbcacea83d72c44e00360ea67ea24a"
TURBO_TOPIC = "0x106f923f993c2149d49b4255ff723acafa1f2d94393f561d3eda32ae348f7241"

TOKEN_ARK = "0xCae117ca6Bc8A341D2E7207F30E180f0e5618B9D".lower()
TOKEN_GARK = "0x911f12D137D74E5917877f87cf8A8bB2FDde557f".lower()
TOKEN_USDT = "0x55d398326f99059fF775485246999027B3197955".lower()
USDT_TRANSFER_TOPIC = TRANSFER_TOPIC
RESERVE_USDT_ADDRESSES = {
    "0x1b9f458773d18b4e1aaf5b896721697215c4a68b",
    "0x100844ccd4af887d123c0ac4a9671e0ab5dd9de2",
    "0x23876d9f06f8290f119fb39b7fdcf93a08e2d616",
}
POOL_MONITOR_ADDRESSES = {
    "0x100844ccd4af887d123c0ac4a9671e0ab5dd9de2": "MBR资金",
    "0x23876d9f06f8290f119fb39b7fdcf93a08e2d616": "RBS资金",
    "0x1b9f458773d18b4e1aaf5b896721697215c4a68b": "国库资金",
    "0xd9d1c7dcf7cb6181a61ed0e70f64fe7ddd4b9495": "手续费",
}
ARK_USDT_LP = "0xCAaF3c41a40103a23Eeaa4BbA468AF3cF5b0e0D8".lower()
DECIMALS = 18

BONUS_POOL = "0x8501168656FcaC4628F6910CcABEA8B64Ebe5BD4".lower()
STAKE_POOL = "0xd1D95292F450b665566df4c4255615eF4Ed9BD0B".lower()
TARGET_DYNAMIC = "0x8366a748E02F730911Cb5AB4fd049d2E1e0414b7".lower()
EXCLUDED_DYNAMIC_RECEIVERS = {"0x7dfad978e43d47bae564c2cbba88f280474a7c24"}
BURN_ADDR = "0x0000000000000000000000000000000000000000"
BURN_ADDR2 = "0x000000000000000000000000000000000000dead"
EXCLUDED_BURN_SOURCES = {
    "0x7736b5b84caddb7661d250d10e60e31f3c905c99",
    "0x100844ccd4af887d123c0ac4a9671e0ab5dd9de2",
    BONUS_POOL,
    STAKE_POOL,
}

BATCH_SIZE = 200
# 基准：105553753 (BJT 2026-06-22 00:00:01)
REF_BLOCK = 105553753
BASE_TS = 1782057600.0
BLOCK_SEC = 0.45

RPC_URLS = [
    "https://bsc-mainnet.nodereal.io/v1/70208501917a413bab46cb281fc0997f",
    "https://bsc-mainnet.nodereal.io/v1/1ad9525366ba4b56a0a2b4fef2b2fef7",
    "https://bsc-mainnet.nodereal.io/v1/5a4982439b1c47b5a3239531be775cc9",
    "https://bsc-mainnet.nodereal.io/v1/d96a4e697b0541628f61ae6089a97874",
    "https://bsc-mainnet.nodereal.io/v1/91687987baa549e4a48c18cbbf62a080",
    "https://bsc-mainnet.nodereal.io/v1/3f6c4ec20c324cd9a489196a2937c368",
    "https://rpc.ankr.com/bsc/c9251b3e097417a6e558de2dce53c2d276a591fbd89f2ec9f017392936a5e0b5",
    "https://bsc.mytokenpocket.vip",
]

class RPCManager:
    def __init__(self, urls):
        self.urls = urls
        self.index = 0
        # 记录节点是否支持 JSON-RPC batch，避免每个批次重复试错后再整批 fallback。
        self._batch_capability = {}

    def call(self, method, params, retries=1):
        for _ in range(len(self.urls)):
            url = self.urls[self.index]
            name = url.split("/")[2].split(".")[0]
            if "nodereal" in url:
                key = url.split("/v1/")[-1][:8] if "/v1/" in url else "???"
                name = f"NodeReal-{key}"
            print(f"  [RPC] {name} (#{method})")
            for _ in range(retries):
                try:
                    r = requests.post(url, json={"jsonrpc":"2.0","method":method,"params":params,"id":1}, timeout=5)
                    d = r.json()
                    if "error" in d:
                        err = d.get("error", {}).get("message", "")
                        if any(k in err.lower() for k in ["exceed", "limit", "quota", "429", "rate", "too many"]):
                            break
                        time.sleep(1)
                        continue
                    return d["result"]
                except:
                    break  # 超时/断连 → 立即切换
            self.index = (self.index + 1) % len(self.urls)
        raise Exception("RPC 均不可用")

    def call_batch(self, method, params_list, retries=1):
        """尽量用 JSON-RPC batch 获取同类数据，失败时回退到单笔调用。"""
        params_list = list(params_list)
        if not params_list:
            return []
        for _ in range(len(self.urls)):
            url = self.urls[self.index]
            if self._batch_capability.get(url) is False:
                self.index = (self.index + 1) % len(self.urls)
                continue
            payload = [
                {"jsonrpc": "2.0", "method": method, "params": params, "id": i}
                for i, params in enumerate(params_list)
            ]
            for _ in range(retries):
                try:
                    response = requests.post(url, json=payload, timeout=15)
                    data = response.json()
                    if isinstance(data, list):
                        self._batch_capability[url] = True
                        by_id = {item.get("id"): item.get("result") for item in data}
                        return [by_id.get(i) for i in range(len(params_list))]
                except:
                    break
            self._batch_capability[url] = False
            self.index = (self.index + 1) % len(self.urls)

        # 兼容不支持 batch 的 RPC 节点；宁可慢一些也不能将未验证事件计入总涡轮。
        return [self.call(method, params, retries) for params in params_list]

_rpc = RPCManager(RPC_URLS)

_time_ref_block = REF_BLOCK
_time_ref_ts = BASE_TS
_time_ref_updated = 0

def _rpc_call(method, params, retries=3):
    try:
        return _rpc.call(method, params, retries)
    except:
        return None

def get_transaction_by_hash(tx_hash):
    """读取单笔交易详情；仅用于异常释放提醒解析释放周期。"""
    if not tx_hash:
        return None
    return _rpc_call("eth_getTransactionByHash", [tx_hash], retries=1)

def _refresh_time_ref(force=False):
    global _time_ref_block, _time_ref_ts, _time_ref_updated
    now = time.time()
    if not force and now - _time_ref_updated < 3600:
        return
    latest = _rpc_call("eth_blockNumber", [], retries=1)
    if latest:
        _time_ref_block = int(latest, 16)
        _time_ref_ts = now
        _time_ref_updated = now
        print(f"  [时间校准] ref_block=#{_time_ref_block}")

def estimate_block_time(block_number):
    _refresh_time_ref()
    return datetime.fromtimestamp(_time_ref_ts + (block_number - _time_ref_block) * BLOCK_SEC, BJT).strftime("%Y-%m-%d %H:%M:%S")

def get_balance(token, address, block_hex="latest"):
    """eth_call 查余额（仅在汇总时需要，不高频调用）"""
    data = "0x70a08231" + address[2:].lower().zfill(64)
    r = _rpc_call("eth_call", [{"to": token, "data": data}, block_hex])
    return int(r, 16) if r else 0

def get_total_supply(token, block_hex="latest"):
    """eth_call 查询 ERC-20 totalSupply。"""
    r = _rpc_call("eth_call", [{"to": token, "data": "0x18160ddd"}, block_hex])
    return int(r, 16) if r else 0


def _topic_address(address):
    return "0x" + address[2:].lower().zfill(64)


def _pool_transfer_records(logs, token):
    """提取监控地址与 ARK/USDT 底池的直接交互。"""
    records = []
    seen = set()
    for log in logs or []:
        topics = log.get("topics", [])
        if len(topics) < 3 or len(log.get("data", "0x")) < 66:
            continue
        fr = _topic_addr(topics[1]).lower()
        to = _topic_addr(topics[2]).lower()
        if fr in POOL_MONITOR_ADDRESSES and to == ARK_USDT_LP:
            address, direction = fr, "to_pool"
        elif fr == ARK_USDT_LP and to in POOL_MONITOR_ADDRESSES:
            address, direction = to, "from_pool"
        else:
            continue
        tx = log.get("transactionHash", "").lower()
        log_index = int(log.get("logIndex", "0x0"), 16)
        key = (tx, log_index, token, address)
        if key in seen:
            continue
        seen.add(key)
        records.append({
            "block": int(log["blockNumber"], 16),
            "tx": tx,
            "log_index": log_index,
            "address": address,
            "token": token,
            "direction": direction,
            "value": int(log["data"], 16) / 10**DECIMALS,
            "timestamp": estimate_block_time(int(log["blockNumber"], 16)),
        })
    return records


def reserve_usdt_transfer_detected(from_block, to_block):
    """监听储备金和资金地址的 USDT 转账，并提取底池交互记录。"""
    watched_addresses = set(RESERVE_USDT_ADDRESSES) | set(POOL_MONITOR_ADDRESSES)
    address_topics = [_topic_address(address) for address in watched_addresses]
    base = {
        "fromBlock": hex(from_block),
        "toBlock": hex(to_block),
        "address": TOKEN_USDT,
    }
    outgoing = _rpc_call("eth_getLogs", [{
        **base,
        "topics": [USDT_TRANSFER_TOPIC, address_topics, None],
    }], retries=1) or []
    incoming = _rpc_call("eth_getLogs", [{
        **base,
        "topics": [USDT_TRANSFER_TOPIC, None, address_topics],
    }], retries=1) or []
    all_logs = outgoing + incoming
    records = _pool_transfer_records(all_logs, "USDT")
    reserve_dirty = any(
        len(log.get("topics", [])) >= 3 and (
            _topic_addr(log["topics"][1]).lower() in RESERVE_USDT_ADDRESSES
            or _topic_addr(log["topics"][2]).lower() in RESERVE_USDT_ADDRESSES
        )
        for log in all_logs
    )
    if all_logs:
        print(
            f"  [储备金监听] #{from_block}~#{to_block} "
            f"检测到 {len(all_logs)} 笔 USDT 转账"
        )
    return reserve_dirty, records

def _classify_logs(logs, from_block, to_block):
    """解析 ARK logs，按地址分类，返回 (已分类, 未分类原始log)"""
    results = []
    raw_records = []
    for log in logs:
        bn = int(log["blockNumber"], 16)
        tx = log.get("transactionHash", "")
        fr = "0x" + log["topics"][1][26:]
        to = "0x" + log["topics"][2][26:]
        val = int(log["data"], 16) / 10**DECIMALS
        ts = estimate_block_time(bn)

        if fr == BONUS_POOL and to == BURN_ADDR:
            etype = "permanent_bonus"
        elif fr == STAKE_POOL and to == BURN_ADDR:
            etype = "permanent_stake"
        elif to == BURN_ADDR and fr not in EXCLUDED_BURN_SOURCES:
            etype = "burn_stake"
        elif fr == TOKEN_ARK and to == BONUS_POOL:
            etype = "bonus_in"
        elif fr == BONUS_POOL and to == STAKE_POOL:
            etype = "transfer_720"
        elif fr == BONUS_POOL:
            etype = "bonus_withdraw"
        elif fr == TARGET_DYNAMIC and to not in EXCLUDED_DYNAMIC_RECEIVERS:
            etype = "dynamic"
        elif to == STAKE_POOL:
            etype = "stake_in"
        elif fr == STAKE_POOL:
            etype = "stake_out"
        else:
            raw_records.append({
                "block": bn, "tx": tx,
                "from": fr, "to": to, "value": val, "timestamp": ts,
            })
            continue

        results.append({
            "block": bn, "tx": tx, "type": etype,
            "from": fr, "to": to, "value": val, "timestamp": ts,
        })
    return results, raw_records

def _topic_addr(topic):
    return "0x" + topic[-40:]

def _ark_transfer_index(logs):
    """按交易索引 ARK Transfer，保留 wei 精度供涡轮事件交叉校验。"""
    transfers = {}
    for log in logs or []:
        topics = log.get("topics", [])
        data = log.get("data", "0x")
        if len(topics) < 3 or len(data) < 66:
            continue
        tx = log.get("transactionHash", "").lower()
        if not tx:
            continue
        transfers.setdefault(tx, []).append((
            _topic_addr(topics[1]).lower(),
            _topic_addr(topics[2]).lower(),
            int(data, 16),
        ))
    return transfers

def _uint256_words(data):
    body = data[2:] if data.startswith("0x") else data
    return [int(body[i:i+64], 16) for i in range(0, len(body), 64) if body[i:i+64]]

def _parse_lp_swap_logs(logs):
    swaps = []
    for log in logs:
        words = _uint256_words(log.get("data", "0x"))
        if len(words) < 4:
            continue
        usdt_in, ark_in, usdt_out, ark_out = [v / 10**DECIMALS for v in words[:4]]
        amount_usdt = usdt_in or usdt_out
        amount_ark = ark_in or ark_out
        if ark_out > 0 and usdt_in > 0:
            side = "buy_ark"
        elif ark_in > 0 and usdt_out > 0:
            side = "sell_ark"
        else:
            side = "swap"
        price_usdt = amount_usdt / amount_ark if amount_ark else 0
        bn = int(log["blockNumber"], 16)
        swaps.append({
            "block": bn,
            "tx": log.get("transactionHash", ""),
            "log_index": int(log.get("logIndex", "0x0"), 16),
            "side": side,
            "sender": _topic_addr(log["topics"][1]) if len(log.get("topics", [])) > 1 else "",
            "to": _topic_addr(log["topics"][2]) if len(log.get("topics", [])) > 2 else "",
            "usdt_in": usdt_in,
            "usdt_out": usdt_out,
            "ark_in": ark_in,
            "ark_out": ark_out,
            "amount_usdt": amount_usdt,
            "amount_ark": amount_ark,
            "price_usdt": price_usdt,
            "timestamp": estimate_block_time(bn),
        })
    return swaps


class EventParser:
    """解析批量链上事件（每 200 区块通常 3 次 eth_getLogs）"""

    def __init__(self):
        self.events = []
        self._balance_cache = {}

    def process_batch(self, from_block, to_block, query_gark=True):
        """批量 eth_getLogs，提取 ARK/gARK Transfer 事件"""
        results = []

        # 1. ARK/gARK 的 Transfer 事件可在同一 getLogs 过滤器中查询。
        # 节点不支持地址数组时，回退为原来的两次查询，不改变解析结果。
        token_transfer_logs = _rpc_call("eth_getLogs", [{
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
            "address": [TOKEN_ARK, TOKEN_GARK],
            "topics": [TRANSFER_TOPIC]
        }])
        if token_transfer_logs is None:
            ark_logs = _rpc_call("eth_getLogs", [{
                "fromBlock": hex(from_block), "toBlock": hex(to_block),
                "address": TOKEN_ARK, "topics": [TRANSFER_TOPIC]
            }])
            gark_logs = _rpc_call("eth_getLogs", [{
                "fromBlock": hex(from_block), "toBlock": hex(to_block),
                "address": TOKEN_GARK, "topics": [TRANSFER_TOPIC]
            }]) if query_gark else []
        else:
            ark_logs = [log for log in token_transfer_logs if log.get("address", "").lower() == TOKEN_ARK]
            gark_logs = [log for log in token_transfer_logs if log.get("address", "").lower() == TOKEN_GARK] if query_gark else []
        ark_transfers = _ark_transfer_index(ark_logs)
        if ark_logs:
            classified, raw = _classify_logs(ark_logs, from_block, to_block)
            results.extend(classified)
            if raw:
                from db import insert_raw_logs_batch
                insert_raw_logs_batch(raw)

        # 2. gARK Transfer（静态释放的识别依据）
        static_release_txs = set()
        if gark_logs and query_gark:
            for log in gark_logs:
                to = "0x" + log["topics"][2][26:]
                if to in (BURN_ADDR, BURN_ADDR2):
                    bn = int(log["blockNumber"], 16)
                    tx = log.get("transactionHash", "")
                    fr = "0x" + log["topics"][1][26:]
                    val = int(log["data"], 16) / 10**DECIMALS
                    ts = estimate_block_time(bn)
                    static_release_txs.add(tx)
                    results.append({
                        "block": bn, "tx": tx, "type": "static_burn",
                        "from": fr, "to": to, "value": val, "timestamp": ts,
                    })

        # 3. Release 与 Turbo 都来自动态合约，可合并为一次日志查询。
        dynamic_event_logs = _rpc_call("eth_getLogs", [{
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
            "address": TARGET_DYNAMIC,
            "topics": [[RELEASE_TOPIC, TURBO_TOPIC]],
        }])
        if dynamic_event_logs is None:
            release_logs = _rpc_call("eth_getLogs", [{
                "fromBlock": hex(from_block), "toBlock": hex(to_block),
                "address": TARGET_DYNAMIC, "topics": [RELEASE_TOPIC],
            }])
            turbo_logs = _rpc_call("eth_getLogs", [{
                "fromBlock": hex(from_block), "toBlock": hex(to_block),
                "address": TARGET_DYNAMIC, "topics": [TURBO_TOPIC],
            }])
        else:
            release_logs = [log for log in dynamic_event_logs if log.get("topics", [""])[0].lower() == RELEASE_TOPIC]
            turbo_logs = [log for log in dynamic_event_logs if log.get("topics", [""])[0].lower() == TURBO_TOPIC]

        # Release 的 data[0] 是本次释放 ARK 数量；同交易有 gARK 销毁则为静态释放。
        if release_logs:
            for log in release_logs:
                raw_data = log.get("data", "0x")[2:]
                if len(raw_data) < 64:
                    continue
                bn = int(log["blockNumber"], 16)
                tx = log.get("transactionHash", "")
                val = int(raw_data[:64], 16) / 10**DECIMALS
                results.append({
                    "block": bn, "tx": tx,
                    "type": "release_static" if tx in static_release_txs else "release_dynamic",
                    "from": TARGET_DYNAMIC,
                    "to": _topic_addr(log["topics"][1]) if len(log.get("topics", [])) > 1 else "",
                    "value": val, "timestamp": estimate_block_time(bn),
                })

        # 总涡轮是独立的涡轮事件，不等于静态/动态释放之和。
        if turbo_logs:
            for log in turbo_logs:
                raw_data = log.get("data", "0x")[2:]
                topics = log.get("topics", [])
                if len(raw_data) < 64 or len(topics) < 2:
                    continue
                bn = int(log["blockNumber"], 16)
                tx_hash = log.get("transactionHash", "").lower()
                user = _topic_addr(topics[1]).lower()
                amount_wei = int(raw_data[:64], 16)
                valid_transfer = any(
                    from_addr == TARGET_DYNAMIC and to_addr == user and value == amount_wei
                    for from_addr, to_addr, value in ark_transfers.get(tx_hash, [])
                )
                # 静态释放会经过固定接收地址 0x7df...，不计入总涡轮。
                # Turbo 事件、动态合约转账路径和金额必须同时匹配。
                if user in EXCLUDED_DYNAMIC_RECEIVERS or not valid_transfer:
                    print(
                        f"  [忽略非总涡轮] {tx_hash[:12]} "
                        f"receiver={'static' if user in EXCLUDED_DYNAMIC_RECEIVERS else 'mismatch'} "
                        f"transfer={'ok' if valid_transfer else 'bad'}"
                    )
                    continue
                results.append({
                    "block": bn, "tx": tx_hash, "type": "turbo_total",
                    "from": TARGET_DYNAMIC, "to": user,
                    "value": amount_wei / 10**DECIMALS,
                    "timestamp": estimate_block_time(bn),
                })

        # 4. ARK/USDT LP Swap logs
        lp_logs = _rpc_call("eth_getLogs", [{
            "fromBlock": hex(from_block),
            "toBlock": hex(to_block),
            "address": ARK_USDT_LP,
            "topics": [SWAP_TOPIC]
        }])
        if lp_logs:
            swaps = _parse_lp_swap_logs(lp_logs)
            if swaps:
                from db import insert_lp_swaps_batch
                insert_lp_swaps_batch(swaps)
                print(f"  [LP] #{from_block}~#{to_block} {len(swaps)} swaps")

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
