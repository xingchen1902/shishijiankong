#!/usr/bin/env python3
"""
ARK 实时监控入口
- 省 CU 模式：eth_getLogs 查批量（每 200 块一批）
- 余额缓存，不高频调 eth_call
"""

import json, os, sys, time, threading
from datetime import datetime, timezone, timedelta

from db import init_db, get_conn, get_monitor_state, set_monitor_state
from ws_listener import BlockListener
from event_parser import EventParser, reserve_usdt_transfer_detected, get_balance, TOKEN_USDT, DECIMALS
from aggregator import DailyAggregator
from pusher import push_mbr_transaction_alert

BJT = timezone(timedelta(hours=8))
MBR_ADDRESS = "0x100844ccd4af887d123c0ac4a9671e0ab5dd9de2"
MBR_ALERT_STATE_KEY = "mbr_alerted_transactions"


def _load_mbr_alerted_transactions():
    try:
        value = get_monitor_state(MBR_ALERT_STATE_KEY)
        data = json.loads(value or "[]")
        return set(data if isinstance(data, list) else [])
    except (TypeError, ValueError):
        return set()


def _save_mbr_alerted_transactions(transactions):
    set_monitor_state(MBR_ALERT_STATE_KEY, json.dumps(list(transactions)[-5000:]))

def main():
    print("=" * 50)
    print("ARK 实时监控启动（批量模式，每200块一批）")
    print(f"BJT: {datetime.now(BJT).strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    init_db()

    aggregator = DailyAggregator()
    parser = EventParser()
    mbr_alerted_transactions = _load_mbr_alerted_transactions()

    def on_batch(from_block, to_block):
        query_gark = True
        events = parser.process_batch(from_block, to_block, query_gark=query_gark)
        if events:
            aggregator.add_events(events)
        # 储备金余额平时使用长缓存；仅在相关 USDT Transfer 出现后通知 API 刷新。
        reserve_dirty, pool_usdt_records = reserve_usdt_transfer_detected(from_block, to_block)
        if pool_usdt_records:
            from db import insert_pool_address_events_batch
            insert_pool_address_events_batch(pool_usdt_records)
            print(f"  [底池监控] #{from_block}~#{to_block} 记录 {len(pool_usdt_records)} 笔 USDT 交互")
            mbr_records = [
                record for record in pool_usdt_records
                if record.get("address") == MBR_ADDRESS
                and record.get("token") == "USDT"
                and record.get("tx") not in mbr_alerted_transactions
            ]
            if mbr_records:
                try:
                    mbr_balance = get_balance(TOKEN_USDT, MBR_ADDRESS) / (10 ** DECIMALS)
                except Exception as exc:
                    print(f"  [MBR交易提醒] 查询余额失败: {exc}")
                    mbr_balance = 0
                for record in mbr_records:
                    if push_mbr_transaction_alert(record, mbr_balance):
                        mbr_alerted_transactions.add(record["tx"])
                _save_mbr_alerted_transactions(mbr_alerted_transactions)
        if reserve_dirty:
            set_monitor_state("reserve_balance_dirty_at", time.time())
        # 每批写入数据库
        aggregator.flush_events()
        # 基于已落库事件计算集中提醒，不产生额外链上请求。
        aggregator.process_burst_alerts()
        # 仅在该区块范围已完成解析并落库后推进检查点；重启时无需按事件最大区块重扫。
        set_monitor_state("event_parser_last_block", to_block)
        aggregator.check_date_change()

    listener = BlockListener(on_batch_callback=on_batch)

    try:
        if len(sys.argv) > 1:
            start = int(sys.argv[1])
        else:
            # 优先使用已完整解析并落库的精确检查点；旧数据库没有检查点时兼容最大事件区块。
            checkpoint = get_monitor_state("event_parser_last_block")
            start = int(checkpoint) if checkpoint else 0
            if not start:
                conn = get_conn()
                db_max = conn.execute("SELECT MAX(block) FROM events").fetchone()[0]
                conn.close()
                start = db_max if db_max else 0
            if start:
                source = "检查点" if checkpoint else "DB 最大事件区块"
                print(f"[启动] 从{source} #{start} 继续监听")
        listener.start(start_block=start)
    except KeyboardInterrupt:
        aggregator.flush_events()
        listener.stop()
        print("\n监控已停止")

if __name__ == "__main__":
    main()
