from __future__ import annotations

import uuid
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.broker.oanda import OandaClient
from app.config import get_settings
from app.ledger.trades import (
    get_trade_intent_by_idempotency,
    insert_trade_intent,
    update_trade_intent,
)
from app.logging.logger import get_logger, log_event

router = APIRouter()
execution_logger = get_logger("execution", "logs/execution.jsonl")


class ExecuteRequest(BaseModel):
    symbol: str
    side: Literal["LONG", "SHORT"]
    units: float
    idempotency_key: str


class ExecuteResponse(BaseModel):
    intent_id: str
    status: str
    oanda_order_id: Optional[str] = None
    oanda_trade_id: Optional[str] = None


@router.post("/api/v1/execute", response_model=ExecuteResponse)
async def execute(req: ExecuteRequest) -> ExecuteResponse:
    settings = get_settings()
    existing = get_trade_intent_by_idempotency(req.idempotency_key)
    if existing:
        return ExecuteResponse(
            intent_id=existing["id"],
            status=existing["status"],
            oanda_order_id=existing.get("oanda_order_id"),
            oanda_trade_id=existing.get("oanda_trade_id"),
        )

    intent_id = str(uuid.uuid4())
    request_payload = req.model_dump()

    insert_trade_intent(
        intent_id=intent_id,
        symbol=req.symbol,
        side=req.side,
        units=req.units,
        status="PENDING",
        idempotency_key=req.idempotency_key,
        reason="manual execution",
        requested=request_payload,
    )

    if settings.dry_run:
        update_trade_intent(
            intent_id=intent_id,
            status="DRY_RUN",
            response={"message": "dry run"},
        )
        log_event(execution_logger, "dry_run", intent_id=intent_id, symbol=req.symbol)
        return ExecuteResponse(intent_id=intent_id, status="DRY_RUN")

    client = OandaClient()
    units = req.units if req.side == "LONG" else -abs(req.units)

    try:
        response = client.place_market_order(req.symbol, units)
    except Exception as exc:  # noqa: BLE001
        update_trade_intent(
            intent_id=intent_id,
            status="FAILED",
            response={"error": str(exc)},
        )
        log_event(execution_logger, "execution_failed", intent_id=intent_id, error=str(exc))
        raise HTTPException(status_code=500, detail="Execution failed") from exc

    order_id = response.get("orderCreateTransaction", {}).get("id")
    trade_id = response.get("orderFillTransaction", {}).get("tradeOpened", {}).get("tradeID")

    update_trade_intent(
        intent_id=intent_id,
        status="SUBMITTED",
        response=response,
        oanda_order_id=order_id,
        oanda_trade_id=trade_id,
    )

    log_event(
        execution_logger,
        "execution_submitted",
        intent_id=intent_id,
        symbol=req.symbol,
        side=req.side,
        units=req.units,
        order_id=order_id,
        trade_id=trade_id,
    )

    return ExecuteResponse(
        intent_id=intent_id,
        status="SUBMITTED",
        oanda_order_id=order_id,
        oanda_trade_id=trade_id,
    )
