#!/usr/bin/env python3
"""
ARK 实时监控入口
- 省 CU 模式：eth_getLogs 查批量（每 200 块一批）
- 余额缓存，不高频调 eth_call
"""

import os, sys, time, threading
from datetime import datetime, timezone, timedelta

from db import init_db
from db import get_conn
from ws_listener import BlockListener
from event_parser import EventParser
from aggregator import DailyAggregator

BJT = timezone(timedelta(hours=8))

def main():
    print("=" * 50)
    print("ARK 实时监控启动（批量模式，每200块一批）")
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
        if len(sys.argv) > 1:
            start = int(sys.argv[1])
        else:
            # 默认从 DB 最大区块继续（避免重启时错过衔接区块）
            conn = get_conn()
            db_max = conn.execute("SELECT MAX(block) FROM events").fetchone()[0]
            conn.close()
            start = db_max if db_max else 0
            if start:
                print(f"[启动] 从 DB 最大区块 #{start} 继续监听")
        listener.start(start_block=start)
    except KeyboardInterrupt:
        aggregator.flush_events()
        listener.stop()
        print("\n监控已停止")

if __name__ == "__main__":
    main()
