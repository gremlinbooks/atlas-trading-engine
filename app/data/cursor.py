from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.data import db


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_last_candle_ts(symbol: str) -> Optional[str]:
    row = db.fetch_one("SELECT last_candle_ts FROM candle_cursor WHERE symbol = ?", (symbol,))
    return row["last_candle_ts"] if row else None


def set_last_candle_ts(symbol: str, ts: str) -> None:
    db.execute(
        """
        INSERT INTO candle_cursor (symbol, last_candle_ts, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            last_candle_ts = excluded.last_candle_ts,
            updated_at = excluded.updated_at
        """,
        (symbol, ts, _utc_now()),
    )
