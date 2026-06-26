#!/usr/bin/env python3
"""
ARK 实时监控入口
- 省 CU 模式：eth_getLogs 查批量（每 10 块一批）
- 余额缓存，不高频调 eth_call
"""

import os, sys, time, threading
from datetime import datetime, timezone, timedelta

from db import init_db
from ws_listener import BlockListener
from event_parser import EventParser
from aggregator import DailyAggregator

BJT = timezone(timedelta(hours=8))

def main():
    print("=" * 50)
    print("ARK 实时监控启动（小批量模式，每10块一批）")
    print(f"BJT: {datetime.now(BJT).strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    init_db()

    aggregator = DailyAggregator()
    parser = EventParser()

    def on_batch(from_block, to_block):
        query_gark = True
        events = parser.process_batch(from_block, to_block, query_gark=query_gark)
        if events:
            aggregator.add_events(events)
        # 每批写入数据库
        aggregator.flush_events()
        aggregator.check_date_change()

    listener = BlockListener(on_batch_callback=on_batch)

    try:
        start = int(sys.argv[1]) if len(sys.argv) > 1 else 0
        listener.start(start_block=start)
    except KeyboardInterrupt:
        aggregator.flush_events()
        listener.stop()
        print("\n监控已停止")

if __name__ == "__main__":
    main()
