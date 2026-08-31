#!/usr/bin/env python3
"""回填历史静态/动态释放事件的 release_period 字段。"""

from db import get_conn, update_release_period
from event_parser import get_release_periods


def main():
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT tx
        FROM events
        WHERE type IN ('release_static', 'release_dynamic')
          AND (release_period IS NULL OR release_period = '')
        ORDER BY id
        """
    ).fetchall()
    conn.close()

    txs = [row["tx"] for row in rows if row["tx"]]
    print(f"[释放周期回填] 待处理 {len(txs)} 笔")
    for start in range(0, len(txs), 100):
        periods = get_release_periods(txs[start:start + 100])
        for tx, period in periods.items():
            update_release_period(tx, period)
        print(f"[释放周期回填] {min(start + 100, len(txs))}/{len(txs)}")


if __name__ == "__main__":
    main()
