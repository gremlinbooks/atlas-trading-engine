from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable, Optional

DB_PATH = Path("./data/trades.db")
SCHEMA_PATH = Path("./app/data/schema.sql")


def get_conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with get_conn() as conn:
        conn.executescript(schema_sql)
        conn.commit()


def execute(query: str, params: Optional[Iterable] = None) -> None:
    with get_conn() as conn:
        conn.execute(query, params or [])
        conn.commit()


def fetch_one(query: str, params: Optional[Iterable] = None) -> Optional[sqlite3.Row]:
    with get_conn() as conn:
        cur = conn.execute(query, params or [])
        return cur.fetchone()


def fetch_all(query: str, params: Optional[Iterable] = None) -> list[sqlite3.Row]:
    with get_conn() as conn:
        cur = conn.execute(query, params or [])
        return cur.fetchall()
