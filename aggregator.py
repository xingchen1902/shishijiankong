#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日汇总逻辑
- 监听累加的事件数据，在每天 BJT 00:00 汇总
- 查询链上余额
- 推送飞书 + Telegram
"""

import time, threading
from datetime import datetime, timezone, timedelta
from db import get_conn, insert_events_batch, upsert_daily_summary, get_all_daily_until_yesterday as get_all_daily
from event_parser import EventParser, get_balance, BONUS_POOL, STAKE_POOL, TOKEN_ARK, DECIMALS
from pusher import push_to_feishu, push_to_telegram

BJT = timezone(timedelta(hours=8))

class DailyAggregator:
    def __init__(self):
        self.event_buffer = []
        self.current_date = datetime.now(BJT).strftime("%Y-%m-%d")
        self.lock = threading.Lock()
        self.parser = EventParser()

    def add_events(self, events):
        with self.lock:
            self.event_buffer.extend(events)

    def flush_events(self):
        with self.lock:
            if not self.event_buffer:
                return
            insert_events_batch(self.event_buffer)
            print("  [存储] 写入", len(self.event_buffer), "条事件")
            self.event_buffer = []

    def check_date_change(self):
        today = datetime.now(BJT).strftime("%Y-%m-%d")
        if today != self.current_date:
            yesterday = self.current_date
            self.current_date = today
            self.flush_events()
            self._pending_push(yesterday)

    def _pending_push(self, date_str):
        now = datetime.now(BJT)
        if now.hour == 0 and now.minute >= 5:
            self.compute_and_push(date_str)
        else:
            self._pending_push_date = date_str
            print(f"  [汇总] {date_str} 等待 00:05 推送")
    def compute_and_push(self, date_str):
        # 防重复推送检查（容器重启后同一天不会推两次）
        if hasattr(self, "_pushed_dates") and date_str in self._pushed_dates:
            print(f"  [汇总] {date_str} 已推送过，跳过")
            return
        if not hasattr(self, "_pushed_dates"):
            self._pushed_dates = set()
        print()
        print("=" * 50)
        print("[汇总] 计算", date_str)

        conn = get_conn()
        row = conn.execute("""
            SELECT
                COALESCE(SUM(CASE WHEN type='bonus_withdraw' THEN value ELSE 0 END),0) as bonus_out,
                COALESCE(SUM(CASE WHEN type='stake_in' THEN value ELSE 0 END),0) as stake_in,
                COALESCE(SUM(CASE WHEN type='stake_out' THEN value ELSE 0 END),0) as stake_out,
                COALESCE(SUM(CASE WHEN type='static_burn' THEN value ELSE 0 END),0) as static_burn,
                COALESCE(SUM(CASE WHEN type='dynamic' THEN value ELSE 0 END),0) as dynamic_in
            FROM events
            WHERE date(created_at) = ? OR (timestamp IS NOT NULL AND timestamp LIKE ?)
        """, (date_str, date_str + "%")).fetchone()
        conn.close()

        if not row or row["bonus_out"] is None:
            print("  [汇总]", date_str, "无数据")
            return

        bonus_out = float(row["bonus_out"])
        stake_in_val = float(row["stake_in"])
        stake_out = float(row["stake_out"])
        static_burn = float(row["static_burn"])
        dynamic_in = float(row["dynamic_in"])
        net_stake = stake_in - stake_out

        bonus_bal = get_balance(TOKEN_ARK, BONUS_POOL) / 10**DECIMALS
        stake_bal = get_balance(TOKEN_ARK, STAKE_POOL) / 10**DECIMALS

        record = {
            "date": date_str,
            "bonus_balance": round(bonus_bal, 2),
            "bonus_withdraw": round(bonus_out, 2),
            "static_burn": round(static_burn, 2),
            "dynamic_in": round(dynamic_in, 2),
            "stake_balance": round(stake_bal, 2),
            "stake_in": round(stake_in_val, 2),
            "stake_out": round(stake_out, 2),
            "net_stake": round(net_stake, 2),
        }

        print("  奖金池提取: %.2f" % bonus_out)
        print("  静态涡轮: %.2f" % static_burn)
        print("  动静态涡轮: %.2f" % dynamic_in)
        print("  新增质押: %.2f" % stake_in_val)
        print("  赎回: %.2f" % stake_out)
        print("  奖金池余额: %.2f" % bonus_bal)
        print("  质押池余额: %.2f" % stake_bal)
        print("  净质押: %.2f" % net_stake)

        upsert_daily_summary(date_str, **{k: v for k, v in record.items() if k != "date"})

        push_to_feishu(record)
        push_to_telegram(record)
        if not hasattr(self, "_pushed_dates"):
            self._pushed_dates = set()
        self._pushed_dates.add(date_str)
        print("=" * 50)
