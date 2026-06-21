#!/usr/bin/env python3
"""
ARK 实时监控入口
- 启动区块监听
- 解析事件并存储
- 每天 BJT 00:00 汇总推送
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
    print("ARK 实时监控启动")
    print(f"BJT: {datetime.now(BJT).strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 50)

    # 初始化数据库
    init_db()

    aggregator = DailyAggregator()
    parser = EventParser()

    # 区块回调
    def on_block(batch_start, batch_end, mode=0):
        events = []
        if mode == 1:
            events = parser.parse_block_range(batch_start, batch_end)
        else:
            events = parser.parse_block_logs(batch_start, batch_end)
        if events:
            aggregator.add_events(events)
            for e in events[-5:]:
                print(f"  [{e['type']}] {e['value']:.4f}  #{e['block']}")
        if events:
            aggregator.flush_events()
            aggregator.check_date_change()
            # 检查等待中的推送（00:30触发）
            if hasattr(aggregator, "_pending_push_date") and aggregator._pending_push_date:
                now_dt = datetime.now(BJT)
                if now_dt.hour == 0 and now_dt.minute >= 30:
                    aggregator._pending_push(aggregator._pending_push_date)


    # 启动监听
    listener = BlockListener(on_block_callback=on_block)

    try:
        # 从 SQLite 最后记录的区块继续监听
        from db import get_conn
        conn = get_conn()
        last_row = conn.execute("SELECT MAX(block) FROM events").fetchone()
        conn.close()
        last_block = int(last_row[0]) if last_row and last_row[0] else 0
        start = int(sys.argv[1]) if len(sys.argv) > 1 else last_block
        print(f"[启动] 最后记录区块: #{last_block}, 从此继续监听")
        listener.start(start_block=start)
    except KeyboardInterrupt:
        aggregator.flush_events()
        listener.stop()
        print("\n监控已停止")


if __name__ == "__main__":
    main()
