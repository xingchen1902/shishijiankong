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
    def on_block(block_num, block, ts):
        events = parser.parse_block_logs(block_num, ts)
        if events:
            aggregator.add_events(events)

            # 打印实时事件
            for e in events[-5:]:
                print(f"  [{e['type']}] {e['value']:.4f}  #{block_num}")

        # 定期写入数据库（每10个区块）
        if block_num % 10 == 0:
            aggregator.flush_events()
            aggregator.check_date_change()

    # 启动监听
    listener = BlockListener(on_block_callback=on_block)

    try:
        # 指定起始区块（从最新开始）
        start = int(sys.argv[1]) if len(sys.argv) > 1 else 0
        listener.start(start_block=start)
    except KeyboardInterrupt:
        aggregator.flush_events()
        listener.stop()
        print("\n监控已停止")


if __name__ == "__main__":
    main()
