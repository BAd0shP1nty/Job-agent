"""SQLite connection management.

All SQL goes through :class:`Database`, which exposes ``connect()`` and a
``transaction()`` context manager (``BEGIN IMMEDIATE``) so that lifecycle moves
are atomic. Swapping to PostgreSQL later means providing another class with the
same two methods and a compatible parameter style in the repository layer.
"""
from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from config.settings import get_settings


class Database:
    def __init__(self, path: Path | str | None = None):
        if path is None:
            path = get_settings().db_path
        self.path = str(path)
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        # A single shared connection keeps ":memory:" databases usable in tests
        # and is safe because every access goes through the lock.
        self._conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=30)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        if self.path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL")
        self._tx_depth = 0

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            yield self._conn

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Atomic unit of work. Nested calls join the outer transaction."""
        with self._lock:
            if self._tx_depth > 0:
                self._tx_depth += 1
                try:
                    yield self._conn
                finally:
                    self._tx_depth -= 1
                return
            self._conn.execute("BEGIN IMMEDIATE")
            self._tx_depth = 1
            try:
                yield self._conn
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")
            finally:
                self._tx_depth = 0

    def close(self) -> None:
        with self._lock:
            self._conn.close()


_db: Database | None = None


def get_database() -> Database:
    global _db
    if _db is None:
        from database.migrations import migrate

        _db = Database()
        migrate(_db)
    return _db


def set_database(db: Database) -> None:
    global _db
    _db = db
