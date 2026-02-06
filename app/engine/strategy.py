from __future__ import annotations

from typing import Any, Dict, Tuple

from app.config import get_settings


def evaluate_candles(candles: list[dict[str, Any]]) -> Tuple[str, str, Dict[str, Any]]:
    if len(candles) < 2:
        return "HOLD", "insufficient candles", {}

    prev_candle = candles[-2]
    last_candle = candles[-1]
    prev_close = prev_candle["c"]
    last_close = last_candle["c"]

    metadata = {
        "prev_time": prev_candle["time"],
        "prev_close": prev_close,
        "last_time": last_candle["time"],
        "last_close": last_close,
    }

    settings = get_settings()
    if settings.force_signal:
        forced = settings.force_signal.upper()
        metadata["forced_signal"] = forced
        return forced, "forced signal override", metadata

    if last_close > prev_close:
        return "LONG", "last close higher than previous close", metadata
    if last_close < prev_close:
        return "SHORT", "last close lower than previous close", metadata
    return "HOLD", "last close equal to previous close", metadata
