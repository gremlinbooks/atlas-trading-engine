from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.data import db


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def insert_snapshot(
    *,
    balance: float,
    nav: float,
    margin_used: float,
    unrealized_pl: float,
    open_trades: list[dict[str, Any]],
) -> None:
    db.execute(
        """
        INSERT INTO snapshots (ts, balance, nav, margin_used, unrealized_pl, open_trades_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            utc_now(),
            balance,
            nav,
            margin_used,
            unrealized_pl,
            json.dumps(open_trades, ensure_ascii=True),
        ),
    )


def insert_decision(
    *,
    symbol: str,
    state: str,
    spread_pips: float,
    candle_ts: str,
    signal: str,
    reason: str,
    metadata: dict[str, Any],
) -> None:
    db.execute(
        """
        INSERT INTO decisions (ts, symbol, state, spread_pips, candle_ts, signal, reason, metadata_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            utc_now(),
            symbol,
            state,
            spread_pips,
            candle_ts,
            signal,
            reason,
            json.dumps(metadata, ensure_ascii=True),
        ),
    )
