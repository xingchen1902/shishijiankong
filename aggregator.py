#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日汇总逻辑
- 监听累加的事件数据，在每天 BJT 00:00 汇总
- BJT 00:05 推送飞书 + Telegram
"""

import json, os, time, threading, requests
from datetime import datetime, timezone, timedelta
from db import (
    get_conn,
    get_dex_daily_snapshot,
    insert_events_batch,
    upsert_daily_summary,
    get_monitor_state,
    set_monitor_state,
    get_all_daily_until_yesterday as get_all_daily,
)
from event_parser import EventParser, get_balance, BONUS_POOL, STAKE_POOL, TOKEN_ARK, DECIMALS
from pusher import push_to_feishu, push_to_telegram, push_burst_alert

BJT = timezone(timedelta(hours=8))

BURST_WINDOW_SECONDS = int(os.environ.get("BURST_WINDOW_SECONDS", "600"))
BURST_UPDATE_SECONDS = int(os.environ.get("BURST_UPDATE_SECONDS", "600"))
BURST_STATE_KEY = "burst_alert_state"
BURST_RULES = {
    "turbo": {
        "title": "涡轮",
        "types": ("turbo_total",),
        "amount_levels": (10000, 20000, 50000),
        "count_levels": (200, 400, 1000),
        "single_threshold": 2000,
    },
    "redeem": {
        "title": "赎回",
        "types": ("stake_out",),
        "amount_levels": (10000, 20000, 50000),
        "count_levels": (50, 100, 250),
        "single_threshold": 2000,
    },
    "release": {
        "title": "释放",
        "types": ("release_static", "release_dynamic"),
        "amount_levels": (10000, 20000, 50000),
        "count_levels": (200, 400, 1000),
        "single_threshold": 2000,
    },
}

class DailyAggregator:
    def __init__(self):
        self.event_buffer = []
        self.current_date = datetime.now(BJT).strftime("%Y-%m-%d")
        self.lock = threading.Lock()
        self.parser = EventParser()
        self._restore_pending_push()

    def _restore_pending_push(self):
        now = datetime.now(BJT)
        if not (now.hour == 0 and now.minute < 5):
            return

        yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        conn = get_conn()
        exists = conn.execute("SELECT id FROM daily_summary WHERE date=?", (yesterday,)).fetchone()
        conn.close()
        if exists:
            self._pending_push_date = yesterday

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

    def process_burst_alerts(self):
        """根据最近窗口内已落库事件发送集中/单笔 Telegram 提醒。"""
        now = datetime.now(BJT)
        window_start = now - timedelta(seconds=BURST_WINDOW_SECONDS)
        start_str = window_start.strftime("%Y-%m-%d %H:%M:%S")
        end_str = now.strftime("%Y-%m-%d %H:%M:%S")
        conn = get_conn()
        try:
            states = self._load_burst_state()
            for key, rule in BURST_RULES.items():
                placeholders = ",".join("?" for _ in rule["types"])
                rows = conn.execute(
                    f"SELECT id, block, tx, type, from_addr, to_addr, value, timestamp FROM events "
                    f"WHERE timestamp >= ? AND timestamp <= ? AND type IN ({placeholders}) "
                    "ORDER BY block, id",
                    (start_str, end_str, *rule["types"]),
                ).fetchall()
                summary = self._burst_summary(rule, rows, conn, start_str, end_str)
                self._send_single_alerts(key, rule, rows, states)
                self._handle_burst_state(key, rule, summary, rows, states, start_str, end_str)
            self._save_burst_state(states)
        finally:
            conn.close()

    @staticmethod
    def _burst_summary(rule, rows, conn, start_str, end_str):
        values = [float(row["value"] or 0) for row in rows]
        count = len(rows)
        amount = sum(values)
        if rule["title"] == "赎回":
            permanent_rows = conn.execute(
                "SELECT id, block, tx, type, value, timestamp FROM events "
                "WHERE timestamp >= ? AND timestamp <= ? AND type='permanent_stake'",
                (start_str, end_str),
            ).fetchall()
            amount = max(amount - sum(float(row["value"] or 0) for row in permanent_rows), 0)
        else:
            permanent_rows = []
        largest = max(rows, key=lambda row: float(row["value"] or 0), default=None)
        static_amount = sum(float(row["value"] or 0) for row in rows if row["type"] == "release_static")
        dynamic_amount = sum(float(row["value"] or 0) for row in rows if row["type"] == "release_dynamic")
        return {
            "amount": amount,
            "count": count,
            "average": amount / count if count else 0,
            "largest": float(largest["value"] or 0) if largest else 0,
            "largest_tx": largest["tx"] if largest else "",
            "largest_address": (largest["to_addr"] or largest["from_addr"]) if largest else "",
            "static_amount": static_amount,
            "dynamic_amount": dynamic_amount,
            "permanent_rows": permanent_rows,
        }

    @staticmethod
    def _level(rule, summary):
        level = 0
        for index, (amount_limit, count_limit) in enumerate(zip(rule["amount_levels"], rule["count_levels"]), 1):
            if summary["amount"] >= amount_limit or summary["count"] >= count_limit:
                level = index
        return level

    def _load_burst_state(self):
        try:
            state = json.loads(get_monitor_state(BURST_STATE_KEY) or "{}")
            if isinstance(state, dict):
                return state
        except (TypeError, ValueError):
            pass
        return {}

    @staticmethod
    def _save_burst_state(state):
        set_monitor_state(BURST_STATE_KEY, json.dumps(state, ensure_ascii=False))

    def _send_single_alerts(self, key, rule, rows, states):
        alert_state = states.setdefault("single", {})
        seen = alert_state.setdefault(key, {})
        cutoff = time.time() - 86400
        for tx, alerted_at in list(seen.items()):
            if float(alerted_at or 0) < cutoff:
                del seen[tx]
        for row in rows:
            value = float(row["value"] or 0)
            tx = row["tx"]
            if value < rule["single_threshold"] or tx in seen:
                continue
            release_type = ""
            if key == "release":
                release_type = "静态释放" if row["type"] == "release_static" else "动态释放"
            message = (
                f"🔔 <b>单笔{rule['title']}异常提醒</b>\n\n"
                f"数量：{value:,.2f} ARK\n"
                f"时间：{row['timestamp'] or '--'}\n"
                f"类型：{release_type or rule['title']}\n"
                f"触发地址：{row['to_addr'] or row['from_addr'] or '--'}\n"
                f"交易：{self._tx_link(tx)}"
            )
            if push_burst_alert(message):
                seen[tx] = time.time()

    def _handle_burst_state(self, key, rule, summary, rows, states, start_str, end_str):
        state = states.setdefault(key, {})
        level = self._level(rule, summary)
        active = bool(state.get("active"))
        if level == 0:
            if active:
                display = self._cumulative_summary(state, summary)
                message = (
                    f"✅ <b>集中{rule['title']}已结束</b>\n\n"
                    f"持续时间：{self._duration_text(state.get('started_at'), end_str)}\n"
                    f"累计数量：{display['amount']:,.2f} ARK\n"
                    f"累计交易：{display['count']} 笔\n"
                    f"最高等级：{state.get('highest_level', state.get('level', 1))}级\n"
                    f"触发地址：{display.get('largest_address') or '--'}\n"
                    f"最大单笔交易：{self._tx_link(display.get('largest_tx'))}"
                )
                if display["static_amount"] or display["dynamic_amount"]:
                    message = message.replace(
                        f"累计数量：{display['amount']:,.2f} ARK\n",
                        f"静态释放：{display['static_amount']:,.2f} ARK\n"
                        f"动态释放：{display['dynamic_amount']:,.2f} ARK\n"
                        f"释放合计：{display['amount']:,.2f} ARK\n",
                    )
                if push_burst_alert(message):
                    state.clear()
            return

        now_ts = time.time()
        started_at = state.get("started_at") or end_str
        if not active:
            state.update({
                "active": True,
                "started_at": started_at,
                "last_push_at": 0,
                "level": 0,
                "highest_level": 0,
                "seen_ids": [],
                "cumulative_amount": 0,
                "cumulative_count": 0,
                "cumulative_static": 0,
                "cumulative_dynamic": 0,
                "cumulative_largest": 0,
                "cumulative_largest_tx": "",
                "cumulative_largest_address": "",
            })
            active = True
        self._accumulate_state(state, rule, summary, rows)
        previous_level = int(state.get("level") or 0)
        title = "集中%s提醒" % rule["title"]
        if level > previous_level:
            prefix = "⚠️" if level == 1 else ("🚨" if level == 2 else "🆘")
            message = self._burst_message(prefix, title, level, self._cumulative_summary(state, summary), started_at, start_str, end_str, "事件开始" if previous_level == 0 else "等级升级")
            if push_burst_alert(message):
                state["last_push_at"] = now_ts
        elif active and now_ts - float(state.get("last_push_at") or 0) >= BURST_UPDATE_SECONDS:
            message = self._burst_message("📊", f"{title}持续中", level, self._cumulative_summary(state, summary), started_at, start_str, end_str, "持续更新")
            if push_burst_alert(message):
                state["last_push_at"] = now_ts
        state["level"] = level
        state["highest_level"] = max(int(state.get("highest_level") or 0), level)

    @staticmethod
    def _accumulate_state(state, rule, summary, rows):
        seen = {int(item) for item in state.get("seen_ids", [])}
        new_rows = [row for row in rows if int(row["id"]) not in seen]
        new_permanent = [row for row in summary["permanent_rows"] if int(row["id"]) not in seen]
        state["seen_ids"] = list(seen | {int(row["id"]) for row in rows} | {int(row["id"]) for row in new_permanent})[-5000:]
        delta = sum(float(row["value"] or 0) for row in new_rows)
        if rule["title"] == "赎回":
            delta -= sum(float(row["value"] or 0) for row in new_permanent)
        state["cumulative_amount"] = max(float(state.get("cumulative_amount") or 0) + delta, 0)
        state["cumulative_count"] = int(state.get("cumulative_count") or 0) + len(new_rows)
        state["cumulative_static"] = float(state.get("cumulative_static") or 0) + sum(
            float(row["value"] or 0) for row in new_rows if row["type"] == "release_static"
        )
        state["cumulative_dynamic"] = float(state.get("cumulative_dynamic") or 0) + sum(
            float(row["value"] or 0) for row in new_rows if row["type"] == "release_dynamic"
        )
        largest = max(new_rows, key=lambda row: float(row["value"] or 0), default=None)
        if largest and float(largest["value"] or 0) > float(state.get("cumulative_largest") or 0):
            state["cumulative_largest"] = float(largest["value"] or 0)
            state["cumulative_largest_tx"] = largest["tx"]
            state["cumulative_largest_address"] = largest["to_addr"] or largest["from_addr"] or ""

    @staticmethod
    def _cumulative_summary(state, current):
        amount = float(state.get("cumulative_amount") or 0)
        count = int(state.get("cumulative_count") or 0)
        return {
            "amount": amount,
            "count": count,
            "average": amount / count if count else 0,
            "largest": float(state.get("cumulative_largest") or current.get("largest", 0)),
            "largest_tx": state.get("cumulative_largest_tx") or current.get("largest_tx", ""),
            "largest_address": state.get("cumulative_largest_address") or current.get("largest_address", ""),
            "static_amount": float(state.get("cumulative_static") or 0),
            "dynamic_amount": float(state.get("cumulative_dynamic") or 0),
        }

    @staticmethod
    def _duration_text(started_at, end_str):
        try:
            start = datetime.strptime(started_at, "%Y-%m-%d %H:%M:%S")
            end = datetime.strptime(end_str, "%Y-%m-%d %H:%M:%S")
            return f"{max(0, int((end - start).total_seconds() // 60))}分钟"
        except (TypeError, ValueError):
            return "--"

    @staticmethod
    def _tx_link(tx):
        tx = str(tx or "")
        if not tx:
            return "--"
        return f'<a href="https://bscscan.com/tx/{tx}">🔗 bscscan.com/tx/...</a>'

    @staticmethod
    def _burst_message(icon, title, level, summary, started_at, start_str, end_str, status):
        lines = [
            f"{icon} <b>{title} · {level}级</b>",
            "",
            f"统计时间：{start_str} - {end_str}",
            f"持续时间：{DailyAggregator._duration_text(started_at, end_str)}",
        ]
        if summary["static_amount"] or summary["dynamic_amount"]:
            lines.extend([
                "",
                f"静态释放：{summary['static_amount']:,.2f} ARK",
                f"动态释放：{summary['dynamic_amount']:,.2f} ARK",
                f"释放合计：{summary['amount']:,.2f} ARK",
            ])
        else:
            lines.extend(["", f"累计数量：{summary['amount']:,.2f} ARK"])
        lines.extend([
            f"累计交易：{summary['count']} 笔",
            f"平均单笔：{summary['average']:,.2f} ARK",
            f"最大单笔：{summary['largest']:,.2f} ARK",
            f"触发地址：{summary['largest_address'] or '--'}",
            f"状态：{status}",
        ])
        if summary["largest_tx"]:
            lines.extend(["", f"最大单笔交易：\n{DailyAggregator._tx_link(summary['largest_tx'])}"])
        return "\n".join(lines)

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
        now = datetime.now(BJT)
        if now.hour == 0 and now.minute >= 5:
            self.compute_and_push(date_str, do_push=True)
        else:
            self.compute_and_push(date_str, do_push=False)
            self._pending_push_date = date_str

    def _get_dex_snapshot(self, date_str):
        snapshot = get_dex_daily_snapshot(date_str)
        if snapshot:
            return snapshot
        try:
            r = requests.get(
                "http://127.0.0.1:8899/api/dex/capture-pool",
                params={"date": date_str},
                timeout=20,
            )
            d = r.json()
            if d.get("status") == "ok":
                return d.get("data")
        except Exception as e:
            print(f"  [Dex] 快照获取失败: {e}")
        return None

    def _attach_dex_snapshot(self, record):
        snapshot = self._get_dex_snapshot(record["date"])
        if not snapshot:
            print(f"  [Dex] {record['date']} 无底池快照")
        else:
            record["pool_ark"] = round(float(snapshot.get("pool_ark") or 0), 2)
            record["pool_usdt"] = round(float(snapshot.get("pool_usdt") or 0), 2)
            record["ark_price"] = round(float(snapshot.get("price_usd") or 0), 6)
            prev_date = (datetime.strptime(record["date"], "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
            prev_snapshot = get_dex_daily_snapshot(prev_date)
            if prev_snapshot:
                record["pool_ark_delta"] = round(
                    record["pool_ark"] - float(prev_snapshot.get("pool_ark") or 0), 2
                )
                record["pool_usdt_delta"] = round(
                    record["pool_usdt"] - float(prev_snapshot.get("pool_usdt") or 0), 2
                )
                record["ark_price_delta"] = round(
                    record["ark_price"] - float(prev_snapshot.get("price_usd") or 0), 6
                )
            print(
                "  [Dex] 底池 ARK %.2f / USDT %.2f / 价格 %.6f"
                % (record["pool_ark"], record["pool_usdt"], record["ark_price"])
            )

        conn = get_conn()
        next_date = (datetime.strptime(record["date"], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        row = conn.execute("""
            SELECT
                COALESCE(SUM(CASE WHEN side='buy_ark' THEN amount_usdt ELSE 0 END),0) as buy_value_usdt,
                COALESCE(SUM(CASE WHEN side='sell_ark' THEN amount_usdt ELSE 0 END),0) as sell_value_usdt
            FROM lp_swaps
            WHERE timestamp >= ? AND timestamp < ?
        """, (record["date"] + " 00:00:00", next_date + " 00:00:00")).fetchone()
        conn.close()
        record["buy_value_usdt"] = round(float(row["buy_value_usdt"]) if row else 0, 2)
        record["sell_value_usdt"] = round(float(row["sell_value_usdt"]) if row else 0, 2)
        print(
            "  [Dex] 买入价值 %.2f / 卖出价值 %.2f"
            % (record["buy_value_usdt"], record["sell_value_usdt"])
        )
        return record

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
        next_date = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        row = conn.execute("""
            SELECT
                -- 奖金池提取以奖金池全部转出为基数，再排除永久质押和转 720 天。
                COALESCE(SUM(CASE WHEN lower(from_addr)=? THEN value ELSE 0 END),0) as bonus_out_all,
                COALESCE(SUM(CASE WHEN type='stake_in' THEN value ELSE 0 END),0) as stake_in,
                COALESCE(SUM(CASE WHEN type='burn_stake' THEN value ELSE 0 END),0) as burn_stake,
                -- 赎回以质押池全部转出为基数，再排除本金永久质押。
                COALESCE(SUM(CASE WHEN lower(from_addr)=? THEN value ELSE 0 END),0) as stake_out_all,
                COALESCE(SUM(CASE WHEN type='permanent_bonus' THEN value ELSE 0 END),0) as permanent_bonus,
                COALESCE(SUM(CASE WHEN type='permanent_stake' THEN value ELSE 0 END),0) as permanent_stake,
                COALESCE(SUM(CASE WHEN type='release_static' THEN value ELSE 0 END),0) as static_burn,
                COALESCE(SUM(CASE WHEN type='turbo_total' THEN value ELSE 0 END),0) as dynamic_in,
                COALESCE(SUM(CASE WHEN type='release_dynamic' THEN value ELSE 0 END),0) as dynamic_release,
                COALESCE(SUM(CASE WHEN type='transfer_720' THEN value ELSE 0 END),0) as transfer_720,
                COALESCE(SUM(CASE WHEN type='bonus_in' THEN value ELSE 0 END),0) as bonus_in
            FROM events
            WHERE timestamp >= ? AND timestamp < ?
        """, (BONUS_POOL, STAKE_POOL, date_str + " 00:00:00", next_date + " 00:00:00")).fetchone()
        conn.close()

        if not row or row["bonus_out_all"] is None:
            print("  [汇总]", date_str, "无数据")
            return

        # 奖金池提取不包含转 720 天和收益永久质押（奖金池 → 黑洞）。
        bonus_out_all = float(row["bonus_out_all"])
        stake_in_val = float(row["stake_in"]) + float(row["burn_stake"])
        stake_out_all = float(row["stake_out_all"])
        permanent_bonus = float(row["permanent_bonus"])
        permanent_stake = float(row["permanent_stake"])
        static_burn = float(row["static_burn"])
        dynamic_in = float(row["dynamic_in"])
        dynamic_release = float(row["dynamic_release"])
        transfer_720 = float(row["transfer_720"]) if row["transfer_720"] else 0
        bonus_in = float(row["bonus_in"]) if row["bonus_in"] else 0
        bonus_out = max(bonus_out_all - permanent_bonus - transfer_720, 0)
        stake_out = max(stake_out_all - permanent_stake, 0)

        # 前一日余额作为基准
        prev_date = (datetime.strptime(date_str, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        from db import get_conn as gc
        pc = gc()
        prev = pc.execute("SELECT * FROM daily_summary WHERE date=?", (prev_date,)).fetchone()
        pc.close()
        base_bonus = float(prev["bonus_balance"]) if prev else 0
        base_stake = float(prev["stake_balance"]) if prev else 0

        # 公式推算余额（0 RPC 依赖）
        bonus_bal = base_bonus + bonus_in - bonus_out - permanent_bonus - transfer_720
        stake_bal = base_stake + float(row["stake_in"]) + transfer_720 - stake_out - permanent_stake
        net_stake = stake_in_val - stake_out

        record = {
            "date": date_str,
            "bonus_balance": round(bonus_bal, 2),
            "bonus_withdraw": round(bonus_out, 2),
            "static_burn": round(static_burn, 2),
            "dynamic_in": round(dynamic_in, 2),
            "dynamic_release": round(dynamic_release, 2),
            "transfer_720": round(transfer_720, 2),
            "stake_balance": round(stake_bal, 2),
            "stake_in": round(stake_in_val, 2),
            "burn_stake": round(float(row["burn_stake"]), 2),
            "stake_out": round(stake_out, 2),
            "net_stake": round(net_stake, 2),
            "permanent_bonus": round(permanent_bonus, 2),
            "permanent_stake": round(permanent_stake, 2),
        }

        print("  奖金池提取: %.2f" % bonus_out)
        print("  静态释放: %.2f" % static_burn)
        print("  总涡轮: %.2f" % dynamic_in)
        print("  新增质押: %.2f" % stake_in_val)
        print("  赎回: %.2f" % stake_out)
        print("  奖金池余额: %.2f" % bonus_bal)
        print("  质押池余额: %.2f" % stake_bal)
        print("  净质押: %.2f" % net_stake)

        upsert_daily_summary(date_str, **{k: v for k, v in record.items() if k != "date"})

        if do_push:
            record = self._attach_dex_snapshot(record)
            push_to_feishu(record)
            push_to_telegram(record)
            if not hasattr(self, "_pushed_dates"):
                self._pushed_dates = set()
            self._pushed_dates.add(date_str)
        print("=" * 50)
