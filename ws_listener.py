#!/usr/bin/env python3
"""
BSC 区块监听器（HTTP 轮询 0.45s/次）
- safe_block = latest - 1 防重组
- 每 10 块回调一次批量处理（省 CU）
- gap 自动修复（补齐缺失范围）
- RPC 自动切换
"""

import time, requests
from datetime import datetime, timezone, timedelta

BJT = timezone(timedelta(hours=8))

RPC_URLS = [
    "https://bsc-mainnet.nodereal.io/v1/70208501917a413bab46cb281fc0997f",
    "https://bsc-mainnet.nodereal.io/v1/1ad9525366ba4b56a0a2b4fef2b2fef7",
    "https://rpc.ankr.com/bsc/c9251b3e097417a6e558de2dce53c2d276a591fbd89f2ec9f017392936a5e0b5",
    "https://bsc.mytokenpocket.vip",
]

BATCH_SIZE = 20

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
                            break  # 快速切换RPC
                        time.sleep(1)
                        continue
                    return d["result"]
                except:
                    time.sleep(1)
                    continue
            self.index = (self.index + 1) % len(self.urls)
        raise Exception("RPC 均不可用")

_rpc = RPCManager(RPC_URLS)

class BlockListener:
    def __init__(self, on_batch_callback=None):
        self.callback = on_batch_callback
        self.running = False
        self.last_block = 0

    def get_latest_safe_block(self):
        return int(_rpc.call("eth_blockNumber", []), 16) - 1

    def start(self, start_block=None):
        if start_block and start_block > 0:
            self.last_block = start_block
        else:
            self.last_block = self.get_latest_safe_block()
        # 对齐到整批次边界
        self.last_block = self.last_block - (self.last_block % BATCH_SIZE)
        self.running = True
        print(f"[监听] 从区块 #{self.last_block} 开始（每 {BATCH_SIZE} 块一批）")

        while self.running:
            try:
                safe = self.get_latest_safe_block()
                safe_aligned = safe - (safe % BATCH_SIZE)

                # gap 补齐（按批次）
                if safe_aligned > self.last_block + BATCH_SIZE:
                    gap = safe_aligned - self.last_block
                    print(f"[追赶] 落后 {gap} 个区块...")
                    self._backfill(self.last_block + BATCH_SIZE, safe_aligned)

                # 顺序处理每批
                while self.last_block < safe_aligned:
                    start = self.last_block + 1
                    end = min(start + BATCH_SIZE - 1, safe_aligned)
                    if self.callback:
                        self.callback(start, end)
                    self.last_block = end

                time.sleep(0.45)

            except Exception as e:
                print(f"[监听] 异常: {e}")
                time.sleep(5)

    def _backfill(self, start, end):
        count = 0
        for s in range(start, end + 1, BATCH_SIZE):
            e = min(s + BATCH_SIZE - 1, end)
            if self.callback:
                self.callback(s, e)
                count += 1
            time.sleep(0.02)
        print(f"[补齐] #{start}->#{end} 共 {count} 批")

    def stop(self):
        self.running = False
