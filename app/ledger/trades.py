from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

from app.data import db


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def insert_trade_intent(
    *,
    intent_id: str,
    symbol: str,
    side: str,
    units: float,
    status: str,
    idempotency_key: str,
    reason: str,
    requested: dict[str, Any],
    response: Optional[dict[str, Any]] = None,
    oanda_order_id: Optional[str] = None,
    oanda_trade_id: Optional[str] = None,
) -> None:
    db.execute(
        """
        INSERT INTO trade_intents (
            id, created_at, symbol, side, units, status, idempotency_key,
            reason, oanda_order_id, oanda_trade_id, requested_json, response_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            intent_id,
            utc_now(),
            symbol,
            side,
            units,
            status,
            idempotency_key,
            reason,
            oanda_order_id,
            oanda_trade_id,
            json.dumps(requested, ensure_ascii=True),
            json.dumps(response or {}, ensure_ascii=True),
        ),
    )


def update_trade_intent(
    *,
    intent_id: str,
    status: str,
    response: dict[str, Any],
    oanda_order_id: Optional[str] = None,
    oanda_trade_id: Optional[str] = None,
) -> None:
    db.execute(
        """
        UPDATE trade_intents
        SET status = ?, response_json = ?, oanda_order_id = ?, oanda_trade_id = ?
        WHERE id = ?
        """,
        (
            status,
            json.dumps(response, ensure_ascii=True),
            oanda_order_id,
            oanda_trade_id,
            intent_id,
        ),
    )


def get_trade_intent_by_idempotency(idempotency_key: str) -> Optional[dict[str, Any]]:
    row = db.fetch_one(
        "SELECT * FROM trade_intents WHERE idempotency_key = ?",
        (idempotency_key,),
    )
    return dict(row) if row else None


def upsert_position(
    *,
    symbol: str,
    side: Optional[str],
    units: float,
    avg_price: float,
    oanda_trade_id: Optional[str],
) -> None:
    db.execute(
        """
        INSERT INTO positions (symbol, side, units, avg_price, updated_at, oanda_trade_id)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol) DO UPDATE SET
            side = excluded.side,
            units = excluded.units,
            avg_price = excluded.avg_price,
            updated_at = excluded.updated_at,
            oanda_trade_id = excluded.oanda_trade_id
        """,
        (
            symbol,
            side,
            units,
            avg_price,
            utc_now(),
            oanda_trade_id,
        ),
    )


def list_positions() -> list[dict[str, Any]]:
    rows = db.fetch_all("SELECT * FROM positions")
    return [dict(row) for row in rows]


def get_position(symbol: str) -> Optional[dict[str, Any]]:
    row = db.fetch_one("SELECT * FROM positions WHERE symbol = ?", (symbol,))
    return dict(row) if row else None
