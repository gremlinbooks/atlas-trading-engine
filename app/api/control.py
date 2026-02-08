from __future__ import annotations

import uuid
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.broker.oanda import OandaClient
from app.config import get_settings
from app.api.health import SchedulerSingleton
from app.ledger.trades import (
    get_position,
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


class StrategyUpdateRequest(BaseModel):
    strategy_name: Optional[str] = None
    strategy_enabled: Optional[bool] = None
    strategy_min_hold_bars: Optional[int] = None
    strategy_trend_ema_period: Optional[int] = None
    params: Optional[dict] = None


class AlertRequest(BaseModel):
    alert_id: Optional[str] = None
    symbol: str
    action: Literal["LONG", "SHORT"]
    time: str


class AlertResponse(BaseModel):
    status: str
    idempotency_key: str


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


@router.post("/api/v1/strategy")
async def update_strategy(req: StrategyUpdateRequest) -> dict:
    scheduler = SchedulerSingleton.instance
    payload = req.model_dump(exclude_none=True)
    params = payload.pop("params", None) or {}
    scheduler.evaluator.update_strategy_config(**payload, **params)
    return {"status": "ok", "updated": {**payload, **params}}


@router.post("/api/v1/alert", response_model=AlertResponse)
async def alert(req: AlertRequest) -> AlertResponse:
    settings = get_settings()
    symbol = req.symbol
    action = req.action
    bar_time = req.time

    bar_key = f"alert:{symbol}:{action}:{bar_time}"
    existing = get_trade_intent_by_idempotency(bar_key)
    if existing:
        return AlertResponse(status="ALREADY_EXECUTED", idempotency_key=bar_key)

    alert_key = f"alert:{req.alert_id}" if req.alert_id else bar_key
    if alert_key != bar_key:
        existing = get_trade_intent_by_idempotency(alert_key)
        if existing:
            return AlertResponse(status="ALREADY_EXECUTED", idempotency_key=alert_key)

    intent_id = str(uuid.uuid4())
    insert_trade_intent(
        intent_id=intent_id,
        symbol=symbol,
        side=action,
        units=float(settings.default_units),
        status="PENDING",
        idempotency_key=bar_key,
        reason="tv_alert",
        requested=req.model_dump(),
    )

    if settings.dry_run:
        update_trade_intent(
            intent_id=intent_id,
            status="DRY_RUN",
            response={"message": "dry run"},
        )
        log_event(execution_logger, "alert_dry_run", symbol=symbol, action=action, bar_time=bar_time)
        return AlertResponse(status="DRY_RUN", idempotency_key=bar_key)

    position = get_position(symbol)
    trade_id = position.get("oanda_trade_id") if position else None
    side = position.get("side") if position else None
    force_flip = settings.strategy_force_flip

    client = OandaClient()
    try:
        if side and side != action and force_flip:
            if trade_id:
                client.close_trade(trade_id)
            units = settings.default_units if action == "LONG" else -abs(settings.default_units)
            response = client.place_market_order(symbol, units)
        elif not side:
            units = settings.default_units if action == "LONG" else -abs(settings.default_units)
            response = client.place_market_order(symbol, units)
        else:
            update_trade_intent(
                intent_id=intent_id,
                status="NOOP",
                response={"message": "same-side position exists"},
            )
            return AlertResponse(status="NOOP", idempotency_key=bar_key)
    except Exception as exc:  # noqa: BLE001
        update_trade_intent(
            intent_id=intent_id,
            status="FAILED",
            response={"error": str(exc)},
        )
        log_event(execution_logger, "alert_execution_failed", symbol=symbol, error=str(exc))
        raise HTTPException(status_code=500, detail="Alert execution failed") from exc

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
        "alert_execution_submitted",
        symbol=symbol,
        action=action,
        order_id=order_id,
        trade_id=trade_id,
        bar_time=bar_time,
    )
    return AlertResponse(status="SUBMITTED", idempotency_key=bar_key)
