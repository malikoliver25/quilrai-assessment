"""Task 4 core: token-aware sliding-window rate limiter on sqlite.

One row per (tenant, request timestamp, tokens). Window = 60s.
Eviction of expired rows happens on every check-in, keeping the table
small. sqlite in WAL mode handles concurrent async access from one process.
"""
from __future__ import annotations

import sqlite3
import threading
import time

WINDOW_SECONDS = 60.0
MAX_TOKENS_PER_MINUTE = 50_000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS token_usage (
    tenant     TEXT NOT NULL,
    ts         REAL NOT NULL,
    tokens     INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usage ON token_usage (tenant, ts);
"""


class SlidingWindowLimiter:
    def __init__(self, db_path: str = "task4_router/limiter.db", max_tokens: int = MAX_TOKENS_PER_MINUTE) -> None:
        self.db_path = db_path
        self.max_tokens = max_tokens
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def check_and_record(self, tenant: str, tokens: int, now: float | None = None) -> bool:
        """Return True if the request fits in the window (and record it), else False."""
        now = time.time() if now is None else now
        cutoff = now - WINDOW_SECONDS
        with self._lock:
            self._conn.execute(
                "DELETE FROM token_usage WHERE tenant = ? AND ts < ?", (tenant, cutoff)
            )
            used = self._conn.execute(
                "SELECT COALESCE(SUM(tokens), 0) FROM token_usage WHERE tenant = ?", (tenant,)
            ).fetchone()[0]
            if used + tokens > self.max_tokens:
                self._conn.commit()
                return False
            self._conn.execute(
                "INSERT INTO token_usage (tenant, ts, tokens) VALUES (?, ?, ?)", (tenant, now, tokens)
            )
            self._conn.commit()
            return True

    def used_tokens(self, tenant: str, now: float | None = None) -> int:
        now = time.time() if now is None else now
        with self._lock:
            self._conn.execute("DELETE FROM token_usage WHERE tenant = ? AND ts < ?", (tenant, now - WINDOW_SECONDS))
            self._conn.commit()
            return self._conn.execute(
                "SELECT COALESCE(SUM(tokens), 0) FROM token_usage WHERE tenant = ?", (tenant,)
            ).fetchone()[0]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
