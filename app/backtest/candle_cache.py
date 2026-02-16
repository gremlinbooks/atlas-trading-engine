from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class CandleCache:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_schema(self) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS candles (
                    symbol TEXT NOT NULL,
                    timeframe TEXT NOT NULL,
                    ts TEXT NOT NULL,
                    o REAL NOT NULL,
                    h REAL NOT NULL,
                    l REAL NOT NULL,
                    c REAL NOT NULL,
                    volume INTEGER NOT NULL,
                    PRIMARY KEY (symbol, timeframe, ts)
                )
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_candles_symbol_tf_ts
                ON candles(symbol, timeframe, ts)
                """
            )

    def load_range(
        self,
        *,
        symbol: str,
        timeframe: str,
        from_dt: datetime,
        to_dt: datetime,
    ) -> list[dict[str, Any]]:
        from_ts = _to_iso_z(from_dt)
        to_ts = _to_iso_z(to_dt)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT ts, o, h, l, c, volume
                FROM candles
                WHERE symbol = ? AND timeframe = ? AND ts >= ? AND ts < ?
                ORDER BY ts ASC
                """,
                (symbol, timeframe.upper(), from_ts, to_ts),
            ).fetchall()
        return [
            {
                "time": row["ts"],
                "o": float(row["o"]),
                "h": float(row["h"]),
                "l": float(row["l"]),
                "c": float(row["c"]),
                "volume": int(row["volume"]),
            }
            for row in rows
        ]

    def upsert(self, *, symbol: str, timeframe: str, candles: list[dict[str, Any]]) -> None:
        if not candles:
            return
        payload = [
            (
                symbol,
                timeframe.upper(),
                c["time"],
                float(c["o"]),
                float(c["h"]),
                float(c["l"]),
                float(c["c"]),
                int(c["volume"]),
            )
            for c in candles
        ]
        with self._connect() as conn:
            conn.executemany(
                """
                INSERT INTO candles(symbol, timeframe, ts, o, h, l, c, volume)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, timeframe, ts) DO UPDATE SET
                    o = excluded.o,
                    h = excluded.h,
                    l = excluded.l,
                    c = excluded.c,
                    volume = excluded.volume
                """,
                payload,
            )


def _to_iso_z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
