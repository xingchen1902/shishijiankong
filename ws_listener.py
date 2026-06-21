#!/usr/bin/env python3
"""
BSC 区块监听器（HTTP 轮询 0.45s/次，BSC 出块频率）
"""

import time, requests
from datetime import datetime, timezone, timedelta

BJT = timezone(timedelta(hours=8))

RPC_URLS = [
    "https://bsc-mainnet.nodereal.io/v1/70208501917a413bab46cb281fc0997f",
    "https://bsc.mytokenpocket.vip",
]

class BlockListener:
    def __init__(self, on_block_callback=None):
        self.callback = on_block_callback
        self.running = False
        self.last_block = 0

    def _rpc(self, url, method, params, retries=3):
        for i in range(retries):
            try:
                r = requests.post(url, json={"jsonrpc":"2.0","method":method,"params":params,"id":1}, timeout=20)
                d = r.json()
                if "error" in d:
                    if i<retries-1: time.sleep(1); continue
                    raise Exception(d["error"]["message"])
                return d["result"]
            except Exception as e:
                if i<retries-1: time.sleep(1); continue
                raise

    def latest_block(self):
        for url in RPC_URLS:
            try:
                return int(self._rpc(url, "eth_blockNumber", []), 16)
            except: continue
        raise Exception("RPC 均不可用")

    def get_block(self, bn):
        for url in RPC_URLS:
            try:
                return self._rpc(url, "eth_getBlockByNumber", [hex(bn), True])
            except: continue
        return None

    def start(self, start_block=0):
        self.last_block = start_block or self.latest_block()
        self.running = True
        print(f"[监听] 从区块 #{self.last_block} 开始")
        while self.running:
            try:
                cur = self.latest_block()
                if cur > self.last_block:
                    gap = cur - self.last_block
                    if gap > 5:
                        print(f"[追赶] 落后 {gap} 个区块...")
                    for bn in range(self.last_block+1, cur+1):
                        blk = self.get_block(bn)
                        if blk and self.callback:
                            ts = datetime.fromtimestamp(int(blk["timestamp"],16), BJT)
                            self.callback(bn, blk, ts)
                        time.sleep(0.02)
                    self.last_block = cur
                time.sleep(0.45)
            except Exception as e:
                print(f"[监听] 异常: {e}")
                time.sleep(5)

    def stop(self):
        self.running = False
