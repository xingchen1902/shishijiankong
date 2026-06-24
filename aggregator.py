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

    def _check_yesterday_push(self):
        now = datetime.now(BJT)
        if not (now.hour == 0 and now.minute >= 5):
            return
        # 如果有等待推送的日期，现在就推
        pd = getattr(self, "_pending_push_date", None)
        if pd:
            self._pending_push_date = None
            print(f"  [汇总] {pd} 开始推送")
            self.compute_and_push(pd, do_push=True)
            return
        # 兜底：如果昨天的汇总完全没写过
        from db import get_conn
        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        conn = get_conn()
        exists = conn.execute("SELECT id FROM daily_summary WHERE date=?", (yesterday,)).fetchone()
        conn.close()
        if not exists:
            print(f"[检查] {yesterday} 未汇总，立即推送")
            self.compute_and_push(yesterday)

    def check_date_change(self):
        today = datetime.now(BJT).strftime("%Y-%m-%d")
        if today != self.current_date:
            yesterday = self.current_date
            self.current_date = today
            self.flush_events()
            self._pending_push(yesterday)
        self._check_yesterday_push()

    def _pending_push(self, date_str):
        self.compute_and_push(date_str, do_push=True)
    def compute_and_push(self, date_str, do_push=True):
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
                COALESCE(SUM(CASE WHEN type='dynamic' THEN value ELSE 0 END),0) as dynamic_in,
                COALESCE(SUM(CASE WHEN type='transfer_720' THEN value ELSE 0 END),0) as transfer_720,
                COALESCE(SUM(CASE WHEN type='bonus_in' THEN value ELSE 0 END),0) as bonus_in
            FROM events
            WHERE REPLACE(timestamp, 'T', ' ') LIKE ?
        """, (date_str + "%",)).fetchone()
        conn.close()

        if not row or row["bonus_out"] is None:
            print("  [汇总]", date_str, "无数据")
            return

        bonus_out = float(row["bonus_out"])
        stake_in_val = float(row["stake_in"])
        stake_out = float(row["stake_out"])
        static_burn = float(row["static_burn"])
        dynamic_in = float(row["dynamic_in"])
        transfer_720 = float(row["transfer_720"]) if row["transfer_720"] else 0
        bonus_in = float(row["bonus_in"]) if row["bonus_in"] else 0

        # 前一日余额作为基准
        prev_date = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        from db import get_conn as gc
        pc = gc()
        prev = pc.execute("SELECT * FROM daily_summary WHERE date=?", (prev_date,)).fetchone()
        pc.close()
        base_bonus = float(prev["bonus_balance"]) if prev else 0
        base_stake = float(prev["stake_balance"]) if prev else 0

        # 公式推算余额（0 RPC 依赖）
        bonus_bal = base_bonus + bonus_in - bonus_out - transfer_720
        stake_bal = base_stake + stake_in_val + transfer_720 - stake_out
        net_stake = stake_in_val - stake_out

        record = {
            "date": date_str,
            "bonus_balance": round(bonus_bal, 2),
            "bonus_withdraw": round(bonus_out, 2),
            "static_burn": round(static_burn, 2),
            "dynamic_in": round(dynamic_in, 2),
            "transfer_720": round(transfer_720, 2),
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

        if do_push:
            push_to_feishu(record)
            push_to_telegram(record)
            if not hasattr(self, "_pushed_dates"):
                self._pushed_dates = set()
            self._pushed_dates.add(date_str)
        print("=" * 50)
