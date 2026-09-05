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
    conn = sqlite3.connect(DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    return conn

def init_db():
    conn = get_conn()
    conn.execute("PRAGMA journal_mode=WAL")
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
            release_period TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_events_block ON events(block);
        CREATE INDEX IF NOT EXISTS idx_events_type ON events(type);
        CREATE INDEX IF NOT EXISTS idx_events_created ON events(created_at);
        CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);

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
            dynamic_release REAL DEFAULT 0,
            transfer_720 REAL DEFAULT 0,
            stake_balance REAL DEFAULT 0,
            stake_in REAL DEFAULT 0,
            burn_stake REAL DEFAULT 0,
            stake_out REAL DEFAULT 0,
            net_stake REAL DEFAULT 0,
            permanent_bonus REAL DEFAULT 0,
            permanent_stake REAL DEFAULT 0,
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
        CREATE INDEX IF NOT EXISTS idx_lp_swaps_timestamp ON lp_swaps(timestamp);

        CREATE TABLE IF NOT EXISTS dex_daily_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            price_usd REAL DEFAULT 0,
            pool_ark REAL DEFAULT 0,
            pool_usdt REAL DEFAULT 0,
            liquidity_usd REAL DEFAULT 0,
            pair_address TEXT,
            source_updated_at TEXT,
            snapshot_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_dex_daily_snapshots_date ON dex_daily_snapshots(date);

        CREATE TABLE IF NOT EXISTS monitor_state (
            state_key TEXT PRIMARY KEY,
            state_value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS staking_daily_snapshots (
            date TEXT PRIMARY KEY,
            snapshot_at TEXT NOT NULL,
            payload_json TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS pool_address_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            block INTEGER NOT NULL,
            tx TEXT NOT NULL,
            log_index INTEGER NOT NULL,
            address TEXT NOT NULL,
            token TEXT NOT NULL,
            direction TEXT NOT NULL,
            value REAL NOT NULL,
            timestamp TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(tx, log_index, token, address)
        );
        CREATE INDEX IF NOT EXISTS idx_pool_address_events_timestamp
            ON pool_address_events(timestamp);
        CREATE INDEX IF NOT EXISTS idx_pool_address_events_address
            ON pool_address_events(address, token, timestamp);

        CREATE TABLE IF NOT EXISTS pool_address_daily_summary (
            date TEXT NOT NULL,
            address TEXT NOT NULL,
            name TEXT NOT NULL,
            ark_to_pool REAL DEFAULT 0,
            ark_from_pool REAL DEFAULT 0,
            usdt_to_pool REAL DEFAULT 0,
            usdt_from_pool REAL DEFAULT 0,
            transaction_count INTEGER DEFAULT 0,
            snapshot_at TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY(date, address)
        );
        CREATE INDEX IF NOT EXISTS idx_pool_address_daily_date
            ON pool_address_daily_summary(date);

        CREATE TABLE IF NOT EXISTS ai_daily_reports (
            date TEXT PRIMARY KEY,
            model TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            report_text TEXT,
            report_json TEXT,
            source_json TEXT,
            generated_at TEXT,
            telegram_pushed INTEGER DEFAULT 0,
            feishu_pushed INTEGER DEFAULT 0,
            error TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS turbo_pending (
            user_address TEXT PRIMARY KEY,
            eligible_turbo_total REAL DEFAULT 0,
            claimed_total REAL DEFAULT 0,
            pending_total REAL DEFAULT 0,
            over_claimed REAL DEFAULT 0,
            turbo_count INTEGER DEFAULT 0,
            claim_count INTEGER DEFAULT 0,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_turbo_pending_amount
            ON turbo_pending(pending_total);
    """)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(daily_summary)")}
    for column in ("burn_stake", "dynamic_release", "permanent_bonus", "permanent_stake"):
        if column not in columns:
            conn.execute(f"ALTER TABLE daily_summary ADD COLUMN {column} REAL DEFAULT 0")
    # 兼容已有 VPS 数据库：为历史 events 表补充释放周期字段。
    event_columns = {row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()}
    if "release_period" not in event_columns:
        conn.execute("ALTER TABLE events ADD COLUMN release_period TEXT")
    conn.commit()
    conn.close()

def get_monitor_state(state_key):
    conn = get_conn()
    row = conn.execute(
        "SELECT state_value FROM monitor_state WHERE state_key=?", (state_key,)
    ).fetchone()
    conn.close()
    return row["state_value"] if row else None

def set_monitor_state(state_key, state_value):
    conn = get_conn()
    conn.execute("""
        INSERT INTO monitor_state (state_key, state_value, updated_at)
        VALUES (?, ?, datetime('now'))
        ON CONFLICT(state_key) DO UPDATE SET
            state_value=excluded.state_value,
            updated_at=datetime('now')
    """, (state_key, str(state_value)))
    conn.commit()
    conn.close()

def get_ai_daily_report(date_str):
    conn = get_conn()
    row = conn.execute("SELECT * FROM ai_daily_reports WHERE date=?", (date_str,)).fetchone()
    conn.close()
    return dict(row) if row else None

def refresh_turbo_pending():
    """按地址重算已满足12小时的涡轮与奖金池实际提取余额。"""
    now = datetime.now(BJT)
    eligible_before = (now - timedelta(hours=12)).strftime("%Y-%m-%d %H:%M:%S")
    bonus_pool = "0x8501168656fcac4628f6910ccabea8b64ebe5bd4"
    conn = get_conn()
    rows = conn.execute(
        """
        WITH turbo AS (
            SELECT lower(to_addr) AS user_address,
                   SUM(value) AS eligible_turbo_total,
                   COUNT(*) AS turbo_count
            FROM events
            WHERE type='turbo_total'
              AND timestamp IS NOT NULL
              AND timestamp <= ?
              AND to_addr IS NOT NULL
            GROUP BY lower(to_addr)
        ), claims AS (
            SELECT lower(to_addr) AS user_address,
                   SUM(value) AS claimed_total,
                   COUNT(*) AS claim_count
            FROM events
            WHERE type='bonus_withdraw'
              AND lower(from_addr)=?
              AND to_addr IS NOT NULL
            GROUP BY lower(to_addr)
        )
        SELECT COALESCE(t.user_address, c.user_address) AS user_address,
               COALESCE(t.eligible_turbo_total, 0) AS eligible_turbo_total,
               COALESCE(c.claimed_total, 0) AS claimed_total,
               COALESCE(t.turbo_count, 0) AS turbo_count,
               COALESCE(c.claim_count, 0) AS claim_count
        FROM turbo t
        LEFT JOIN claims c ON c.user_address=t.user_address
        UNION ALL
        SELECT c.user_address, 0, c.claimed_total, 0, c.claim_count
        FROM claims c
        LEFT JOIN turbo t ON t.user_address=c.user_address
        WHERE t.user_address IS NULL
        """,
        (eligible_before, bonus_pool),
    ).fetchall()
    conn.execute("DELETE FROM turbo_pending")
    conn.executemany(
        """
        INSERT INTO turbo_pending
            (user_address, eligible_turbo_total, claimed_total, pending_total,
             over_claimed, turbo_count, claim_count, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """,
        [
            (
                row["user_address"],
                float(row["eligible_turbo_total"] or 0),
                float(row["claimed_total"] or 0),
                max(float(row["eligible_turbo_total"] or 0) - float(row["claimed_total"] or 0), 0),
                max(float(row["claimed_total"] or 0) - float(row["eligible_turbo_total"] or 0), 0),
                int(row["turbo_count"] or 0),
                int(row["claim_count"] or 0),
            )
            for row in rows
        ],
    )
    conn.commit()
    result = conn.execute(
        """
        SELECT user_address, pending_total
        FROM turbo_pending
        WHERE pending_total > 0.00000001
        ORDER BY pending_total DESC, user_address
        """
    ).fetchall()
    total = conn.execute(
        "SELECT COALESCE(SUM(pending_total), 0) FROM turbo_pending WHERE pending_total > 0.00000001"
    ).fetchone()[0]
    conn.close()
    return [dict(row) for row in result], float(total or 0)

def get_turbo_pending_snapshot():
    """快速读取待领取汇总；不在 HTTP 请求内扫描历史事件。"""
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT user_address, pending_total
        FROM turbo_pending
        WHERE pending_total > 0.00000001
        ORDER BY pending_total DESC, user_address
        """
    ).fetchall()
    total = conn.execute(
        "SELECT COALESCE(SUM(pending_total), 0) FROM turbo_pending WHERE pending_total > 0.00000001"
    ).fetchone()[0]
    conn.close()
    return [dict(row) for row in rows], float(total or 0)

def save_ai_daily_report(date_str, **kwargs):
    """保存或更新每日 AI 日报状态，支持失败后重试推送。"""
    allowed = {
        "model", "status", "report_text", "report_json", "source_json",
        "generated_at", "telegram_pushed", "feishu_pushed", "error",
    }
    fields = {key: value for key, value in kwargs.items() if key in allowed}
    conn = get_conn()
    existing = conn.execute("SELECT date FROM ai_daily_reports WHERE date=?", (date_str,)).fetchone()
    if existing:
        if fields:
            assignments = ", ".join(f"{key}=?" for key in fields)
            conn.execute(
                f"UPDATE ai_daily_reports SET {assignments}, updated_at=datetime('now') WHERE date=?",
                list(fields.values()) + [date_str],
            )
    else:
        fields.setdefault("status", "pending")
        columns = ["date"] + list(fields.keys())
        placeholders = ", ".join("?" for _ in columns)
        conn.execute(
            f"INSERT INTO ai_daily_reports ({', '.join(columns)}) VALUES ({placeholders})",
            [date_str] + list(fields.values()),
        )
    conn.commit()
    conn.close()

def ensure_dex_daily_snapshots_table():
    conn = get_conn()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS dex_daily_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL UNIQUE,
            price_usd REAL DEFAULT 0,
            pool_ark REAL DEFAULT 0,
            pool_usdt REAL DEFAULT 0,
            liquidity_usd REAL DEFAULT 0,
            pair_address TEXT,
            source_updated_at TEXT,
            snapshot_at TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_dex_daily_snapshots_date ON dex_daily_snapshots(date);
    """)
    conn.commit()
    conn.close()


def insert_staking_daily_snapshot(date_str, snapshot_at, payload):
    """保存北京时间每日质押快照，同一天只保留一份。"""
    conn = get_conn()
    conn.execute(
        """
        INSERT OR IGNORE INTO staking_daily_snapshots
            (date, snapshot_at, payload_json)
        VALUES (?, ?, ?)
        """,
        (date_str, snapshot_at, json.dumps(payload, ensure_ascii=False)),
    )
    conn.commit()
    conn.close()


def get_staking_daily_snapshots(limit=30):
    conn = get_conn()
    rows = conn.execute(
        "SELECT date, snapshot_at, payload_json, created_at "
        "FROM staking_daily_snapshots ORDER BY date DESC LIMIT ?",
        (max(1, min(int(limit or 30), 365)),),
    ).fetchall()
    conn.close()
    result = []
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError):
            payload = {}
        result.append({
            "date": row["date"],
            "snapshot_at": row["snapshot_at"],
            "data": payload,
            "created_at": row["created_at"],
        })
    return result


def insert_pool_address_events_batch(records):
    if not records:
        return
    conn = get_conn()
    conn.executemany(
        """
        INSERT OR IGNORE INTO pool_address_events
            (block, tx, log_index, address, token, direction, value, timestamp)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [(
            row["block"], row["tx"], row["log_index"], row["address"],
            row["token"], row["direction"], row["value"], row.get("timestamp", ""),
        ) for row in records],
    )
    conn.commit()
    conn.close()


def get_pool_address_summary(date_str):
    next_date = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT address,
               COALESCE(SUM(CASE WHEN token='ARK' AND direction='to_pool' THEN value ELSE 0 END), 0) AS ark_to_pool,
               COALESCE(SUM(CASE WHEN token='ARK' AND direction='from_pool' THEN value ELSE 0 END), 0) AS ark_from_pool,
               COALESCE(SUM(CASE WHEN token='USDT' AND direction='to_pool' THEN value ELSE 0 END), 0) AS usdt_to_pool,
               COALESCE(SUM(CASE WHEN token='USDT' AND direction='from_pool' THEN value ELSE 0 END), 0) AS usdt_from_pool,
               COUNT(DISTINCT tx) AS transaction_count
        FROM pool_address_events
        WHERE timestamp >= ? AND timestamp < ?
        GROUP BY address
        """,
        (date_str + " 00:00:00", next_date + " 00:00:00"),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def save_pool_address_daily_summary(date_str, snapshot_at, rows):
    if not rows:
        return
    conn = get_conn()
    conn.executemany(
        """
        INSERT INTO pool_address_daily_summary
            (date, address, name, ark_to_pool, ark_from_pool,
             usdt_to_pool, usdt_from_pool, transaction_count, snapshot_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date, address) DO UPDATE SET
            name=excluded.name,
            ark_to_pool=excluded.ark_to_pool,
            ark_from_pool=excluded.ark_from_pool,
            usdt_to_pool=excluded.usdt_to_pool,
            usdt_from_pool=excluded.usdt_from_pool,
            transaction_count=excluded.transaction_count,
            snapshot_at=excluded.snapshot_at
        """,
        [(
            date_str, row["address"], row["name"], row.get("ark_to_pool", 0),
            row.get("ark_from_pool", 0), row.get("usdt_to_pool", 0),
            row.get("usdt_from_pool", 0), row.get("transaction_count", 0), snapshot_at,
        ) for row in rows],
    )
    conn.commit()
    conn.close()


def get_pool_address_daily_summaries(limit=30):
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM pool_address_daily_summary ORDER BY date DESC, name LIMIT ?",
        (max(1, min(int(limit or 30) * 3, 1095)),),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]

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
        "INSERT INTO events (block, tx, type, from_addr, to_addr, value, timestamp, release_period) VALUES (?,?,?,?,?,?,?,?)",
        (block, tx, event_type, from_addr, to_addr, value, timestamp, None)
    )
    conn.commit()
    conn.close()

def insert_events_batch(events):
    if not events: return
    conn = get_conn()
    data = [(e["block"], e["tx"], e["type"], e.get("from",""), e.get("to",""),
             e["value"], e.get("timestamp",""), e.get("release_period")) for e in events]
    conn.executemany(
        "INSERT INTO events (block, tx, type, from_addr, to_addr, value, timestamp, release_period) VALUES (?,?,?,?,?,?,?,?)",
        data
    )
    conn.commit()
    conn.close()

def update_release_period(tx, period):
    """把异常释放解析出的周期写回对应的释放事件记录。"""
    if not tx:
        return
    conn = get_conn()
    conn.execute(
        """
        UPDATE events
        SET release_period = ?
        WHERE lower(tx) = lower(?)
          AND type IN ('release_static', 'release_dynamic')
        """,
        (period, tx),
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

def get_dex_daily_snapshot(date_str):
    ensure_dex_daily_snapshots_table()
    conn = get_conn()
    row = conn.execute("SELECT * FROM dex_daily_snapshots WHERE date = ?", (date_str,)).fetchone()
    conn.close()
    return dict(row) if row else None

def upsert_dex_daily_snapshot(date_str, **kwargs):
    ensure_dex_daily_snapshots_table()
    conn = get_conn()
    existing = conn.execute("SELECT id FROM dex_daily_snapshots WHERE date = ?", (date_str,)).fetchone()
    if existing:
        fields = ", ".join(f"{k}=?" for k in kwargs)
        vals = list(kwargs.values()) + [date_str]
        conn.execute(f"UPDATE dex_daily_snapshots SET {fields}, updated_at=datetime('now') WHERE date=?", vals)
    else:
        fields = ", ".join(kwargs.keys())
        placeholders = ", ".join("?" for _ in kwargs)
        vals = list(kwargs.values())
        conn.execute(f"INSERT INTO dex_daily_snapshots (date, {fields}) VALUES (?, {placeholders})", [date_str] + vals)
    conn.commit()
    conn.close()

def get_dex_daily_snapshots(limit=20, offset=0):
    ensure_dex_daily_snapshots_table()
    conn = get_conn()
    total_row = conn.execute("SELECT COUNT(*) FROM dex_daily_snapshots").fetchone()
    rows = conn.execute(
        "SELECT * FROM dex_daily_snapshots ORDER BY date DESC LIMIT ? OFFSET ?",
        (limit, offset)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows], (total_row[0] if total_row else 0)

def get_today_events(date_str):
    """获取某天的所有原始事件"""
    next_date = (datetime.strptime(date_str, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    conn = get_conn()
    rows = conn.execute(
        "SELECT * FROM events WHERE timestamp >= ? AND timestamp < ? ORDER BY block",
        (f"{date_str} 00:00:00", f"{next_date} 00:00:00")
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
