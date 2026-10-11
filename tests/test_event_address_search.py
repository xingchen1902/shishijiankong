import os
import tempfile
import unittest
from unittest.mock import patch

import db


class EventAddressSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_dir_patch = patch.object(db, "DB_DIR", self.temp_dir.name)
        self.db_path_patch = patch.object(db, "DB_PATH", os.path.join(self.temp_dir.name, "test.db"))
        self.db_dir_patch.start()
        self.db_path_patch.start()
        self.addCleanup(self.db_path_patch.stop)
        self.addCleanup(self.db_dir_patch.stop)
        self.addCleanup(self.temp_dir.cleanup)

        conn = db.get_conn()
        conn.execute("""
            CREATE TABLE events (
                id INTEGER PRIMARY KEY,
                block INTEGER NOT NULL,
                tx TEXT NOT NULL,
                type TEXT NOT NULL,
                from_addr TEXT,
                to_addr TEXT,
                value REAL NOT NULL,
                timestamp TEXT
            )
        """)
        conn.execute("CREATE INDEX idx_events_from_addr_recent ON events(from_addr COLLATE NOCASE, timestamp DESC, block DESC, id DESC)")
        conn.execute("CREATE INDEX idx_events_to_addr_recent ON events(to_addr COLLATE NOCASE, timestamp DESC, block DESC, id DESC)")
        conn.executemany(
            """INSERT INTO events
               (id, block, tx, type, from_addr, to_addr, value, timestamp)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (1, 10, "tx-a", "turbo_total", "0xAbC", "0xpool", 5, "2026-10-10 10:00:00"),
                (2, 11, "tx-b", "stake_out", "0xpool", "0xabc", 7, "2026-10-10 11:00:00"),
                (3, 12, "tx-c", "bonus_in", "0xother", "0xother2", 9, "2026-10-10 12:00:00"),
                (4, 10, "tx-a", "dynamic", "0xAbC", "0xpool", 5, "2026-10-10 10:00:00"),
                (5, 13, "tx-d", "turbo_contribution", "0xAbC", "0xpool", 2, "2026-10-10 13:00:00"),
            ],
        )
        conn.commit()
        conn.close()

    def test_matches_both_event_addresses_case_insensitively(self):
        result = db.search_events_by_address("0xabc", page=1, per_page=50)

        self.assertEqual(result["total"], 2)
        self.assertEqual([row["tx"] for row in result["data"]], ["tx-b", "tx-a"])
        self.assertEqual([row["type"] for row in result["data"]], ["stake_out", "turbo_total"])

    def test_paginates_matching_events(self):
        first = db.search_events_by_address("0xabc", page=1, per_page=1)
        second = db.search_events_by_address("0xabc", page=2, per_page=1)

        self.assertEqual(first["total"], 2)
        self.assertEqual(first["data"][0]["tx"], "tx-b")
        self.assertEqual(second["data"][0]["tx"], "tx-a")

    def test_filters_by_event_type(self):
        result = db.search_events_by_address("0xabc", page=1, per_page=50, event_type="turbo_total")

        self.assertEqual(result["total"], 1)
        self.assertEqual([row["type"] for row in result["data"]], ["turbo_total"])


if __name__ == "__main__":
    unittest.main()
