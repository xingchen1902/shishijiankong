#!/usr/bin/env python3
"""
SQLite 存储层
- events: 每笔链上事件的明细记录
- daily: 按日期聚合的汇总数据
"""

import os, sqlite3, json
from datetime import datetime, timezone, timedelta

BJT = timezone(timedelta(hours=8))
DB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
DB_PATH = os.path.join(DB_DIR, "ark_monitor.db")

def get_conn():
    os.makedirs(DB_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            block INTEGER NOT NULL,
            tx TEXT NOT NULL,
            type TEXT NOT NULL,
            from_addr TEXT,
            to_addr TEXT,
            value REAL NOT NULL,
            timestamp TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_events_block ON events(block);
        CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
        CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);

        CREATE TABLE IF NOT EXISTS raw_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            block INTEGER NOT NULL,
            tx TEXT NOT NULL,
            from_addr TEXT,
            to_addr TEXT,
            value REAL NOT NULL,
            timestamp TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_raw_block ON raw_logs(block);
        CREATE INDEX IF NOT EXISTS idx_raw_from ON raw_logs(from_addr);
        CREATE INDEX IF NOT EXISTS idx_raw_to ON raw_logs(to_addr);

        CREATE TABLE IF NOT EXISTS daily_summary (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            bonus_balance REAL DEFAULT 0,
            bonus_withdraw REAL DEFAULT 0,
            static_burn REAL DEFAULT 0,
            dynamic_in REAL DEFAULT 0,
            transfer_720 REAL DEFAULT 0,
            stake_balance REAL DEFAULT 0,
            stake_in REAL DEFAULT 0,
            stake_out REAL DEFAULT 0,
            net_stake REAL DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS lp_swaps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            block INTEGER NOT NULL,
            tx TEXT NOT NULL,
            log_index INTEGER NOT NULL,
            side TEXT,
            sender TEXT,
            to_addr TEXT,
            usdt_in REAL DEFAULT 0,
            usdt_out REAL DEFAULT 0,
            ark_in REAL DEFAULT 0,
            ark_out REAL DEFAULT 0,
            amount_usdt REAL DEFAULT 0,
            amount_ark REAL DEFAULT 0,
            price_usdt REAL DEFAULT 0,
            timestamp TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(tx, log_index)
        );
        CREATE INDEX IF NOT EXISTS idx_lp_swaps_block ON lp_swaps(block);
        CREATE INDEX IF NOT EXISTS idx_lp_swaps_side ON lp_swaps(side);
        CREATE INDEX IF NOT EXISTS idx_lp_swaps_created ON lp_swaps(created_at);
    """)
    conn.commit()
    conn.close()

def insert_raw_logs_batch(records):
    if not records: return
    conn = get_conn()
    data = [(r['block'], r['tx'], r.get('from',''), r.get('to',''),
             r['value'], r.get('timestamp','')) for r in records]
    conn.executemany(
        'INSERT INTO raw_logs (block, tx, from_addr, to_addr, value, timestamp) VALUES (?,?,?,?,?,?)',
        data
    )
    conn.commit()
    conn.close()

def insert_event(block, tx, event_type, from_addr, to_addr, value, timestamp):
    conn = get_conn()
    conn.execute(
        "INSERT INTO events (block, tx, type, from_addr, to_addr, value, timestamp) VALUES (?,?,?,?,?,?,?)",
        (block, tx, event_type, from_addr, to_addr, value, timestamp)
    )
    conn.commit()
    conn.close()

def insert_events_batch(events):
    if not events: return
    conn = get_conn()
    data = [(e["block"], e["tx"], e["type"], e.get("from",""), e.get("to",""),
             e["value"], e.get("timestamp","")) for e in events]
    conn.executemany(
        "INSERT INTO events (block, tx, type, from_addr, to_addr, value, timestamp) VALUES (?,?,?,?,?,?,?)",
        data
    )
    conn.commit()
    conn.close()

def insert_lp_swaps_batch(swaps):
    if not swaps: return
    conn = get_conn()
    data = [(
        s["block"], s["tx"], s["log_index"], s.get("side", ""),
        s.get("sender", ""), s.get("to", ""),
        s.get("usdt_in", 0), s.get("usdt_out", 0),
        s.get("ark_in", 0), s.get("ark_out", 0),
        s.get("amount_usdt", 0), s.get("amount_ark", 0),
        s.get("price_usdt", 0), s.get("timestamp", "")
    ) for s in swaps]
    conn.executemany(
        """INSERT OR IGNORE INTO lp_swaps (
            block, tx, log_index, side, sender, to_addr,
            usdt_in, usdt_out, ark_in, ark_out,
            amount_usdt, amount_ark, price_usdt, timestamp
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        data
    )
    conn.commit()
    conn.close()

def get_daily_summary(date_str):
    conn = get_conn()
    row = conn.execute("SELECT * FROM daily_summary WHERE date = ?", (date_str,)).fetchone()
    conn.close()
    return dict(row) if row else None

def upsert_daily_summary(date_str, **kwargs):
    conn = get_conn()
    existing = conn.execute("SELECT * FROM daily_summary WHERE date = ?", (date_str,)).fetchone()
    if existing:
        fields = ", ".join(f"{k}=?" for k in kwargs)
        vals = list(kwargs.values()) + [date_str]
        conn.execute(f"UPDATE daily_summary SET {fields}, updated_at=datetime('now') WHERE date=?", vals)
    else:
        fields = ", ".join(kwargs.keys())
        placeholders = ", ".join("?" for _ in kwargs)
        vals = list(kwargs.values())
        conn.execute(f"INSERT INTO daily_summary (date, {fields}) VALUES (?, {placeholders})", [date_str] + vals)
    conn.commit()
    conn.close()

def get_all_daily_until_yesterday():
    from datetime import datetime, timezone, timedelta
    BJT = timezone(timedelta(hours=8))
    today = datetime.now(BJT).strftime('%Y-%m-%d')
    conn = get_conn()
    rows = conn.execute("SELECT * FROM daily_summary WHERE date < ? ORDER BY date DESC LIMIT 30", (today,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]

def get_today_events(date_str):
    """获取某天的所有原始事件"""
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM events WHERE date(created_at) = ? OR timestamp LIKE ? ORDER BY block",
        (date_str, f"{date_str}%")
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
