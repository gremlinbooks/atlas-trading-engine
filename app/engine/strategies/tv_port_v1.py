from __future__ import annotations

from typing import List

from app.engine.strategy_base import Candle, Strategy, StrategyContext, StrategyDecision


class TVPortV1Strategy(Strategy):
    def evaluate(self, candles: list[Candle], ctx: StrategyContext) -> StrategyDecision:
        config = ctx.config
        if not config.enabled:
            return StrategyDecision("HOLD", "strategy disabled", {}, next_state=ctx.state)

        if len(candles) < max(2, config.trend_ema_period):
            return StrategyDecision("HOLD", "insufficient candles", {}, next_state=ctx.state)

        closes = [c.c for c in candles]
        ema = _ema(closes, config.trend_ema_period)
        last_close = closes[-1]
        allow_long = last_close > ema
        allow_short = last_close < ema

        bars_since_entry = _bars_since_entry(candles, ctx.position.entry_ts)
        if ctx.position.side and bars_since_entry is not None and bars_since_entry < config.min_hold_bars:
            return StrategyDecision(
                "HOLD",
                "min hold active",
                {
                    "bars_since_entry": bars_since_entry,
                    "trend_ema": ema,
                    "allow_long": allow_long,
                    "allow_short": allow_short,
                },
                next_state=ctx.state,
            )

        return StrategyDecision(
            "HOLD",
            "placeholder strategy",
            {
                "trend_ema": ema,
                "allow_long": allow_long,
                "allow_short": allow_short,
            },
            next_state=ctx.state,
        )


def _ema(values: List[float], period: int) -> float:
    if not values:
        return 0.0
    k = 2 / (period + 1)
    ema = values[0]
    for value in values[1:]:
        ema = value * k + ema * (1 - k)
    return ema


def _bars_since_entry(candles: list[Candle], entry_ts: str | None) -> int | None:
    if not entry_ts:
        return None
    for index, candle in enumerate(candles):
        if candle.ts == entry_ts:
            return len(candles) - 1 - index
    return None
