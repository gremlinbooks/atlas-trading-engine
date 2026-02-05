from __future__ import annotations

from app.engine.gates import is_off_hours, is_spread_ok

STATE_HUNTING = "HUNTING"
STATE_OFF_HOURS = "OFF_HOURS"
STATE_SPREAD_BLOCKED = "SPREAD_BLOCKED"
STATE_IN_TRADE = "IN_TRADE"
STATE_FLIP_PENDING = "FLIP_PENDING"
STATE_ERROR_HALT = "ERROR_HALT"


def get_state(
    *,
    symbol: str,
    spread_pips: float,
    position_side: str | None,
    error_halt: bool = False,
) -> str:
    if error_halt:
        return STATE_ERROR_HALT
    if is_off_hours():
        return STATE_OFF_HOURS
    if not is_spread_ok(spread_pips):
        return STATE_SPREAD_BLOCKED
    if position_side:
        return STATE_IN_TRADE
    return STATE_HUNTING
