from __future__ import annotations

import math
from typing import List, Optional

from app.engine.strategy_base import Candle


def ema(values: List[float], period: int) -> List[float]:
    if not values:
        return []
    k = 2 / (period + 1)
    ema_values = [values[0]]
    for value in values[1:]:
        ema_values.append(value * k + ema_values[-1] * (1 - k))
    return ema_values


def sma(values: List[float], period: int) -> List[Optional[float]]:
    if period <= 0:
        return [None for _ in values]
    out: List[Optional[float]] = []
    for i in range(len(values)):
        if i + 1 < period:
            out.append(None)
        else:
            window = values[i + 1 - period : i + 1]
            if any(math.isnan(v) for v in window):
                out.append(None)
            else:
                out.append(sum(window) / period)
    return out


def rsi(values: List[float], period: int) -> List[Optional[float]]:
    if len(values) < 2:
        return [None for _ in values]
    gains: List[float] = [0.0]
    losses: List[float] = [0.0]
    for i in range(1, len(values)):
        delta = values[i] - values[i - 1]
        gains.append(max(delta, 0.0))
        losses.append(max(-delta, 0.0))

    rsi_values: List[Optional[float]] = [None] * len(values)
    avg_gain = sum(gains[1 : period + 1]) / period if len(values) > period else 0.0
    avg_loss = sum(losses[1 : period + 1]) / period if len(values) > period else 0.0

    for i in range(period, len(values)):
        if i > period:
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        if avg_loss == 0:
            rsi_values[i] = 100.0
        else:
            rs = avg_gain / avg_loss
            rsi_values[i] = 100 - (100 / (1 + rs))
    return rsi_values


def atr(candles: List[Candle], period: int) -> List[Optional[float]]:
    if not candles:
        return []
    tr_values: List[float] = [candles[0].h - candles[0].l]
    for i in range(1, len(candles)):
        high = candles[i].h
        low = candles[i].l
        prev_close = candles[i - 1].c
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        tr_values.append(tr)

    atr_values: List[Optional[float]] = [None] * len(candles)
    if len(candles) < period:
        return atr_values

    first_atr = sum(tr_values[:period]) / period
    atr_values[period - 1] = first_atr
    for i in range(period, len(candles)):
        prev_atr = atr_values[i - 1] if atr_values[i - 1] is not None else first_atr
        atr_values[i] = (prev_atr * (period - 1) + tr_values[i]) / period
    return atr_values


def highest(values: List[float], length: int) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    for i in range(len(values)):
        if i + 1 < length:
            out.append(None)
        else:
            out.append(max(values[i + 1 - length : i + 1]))
    return out


def lowest(values: List[float], length: int) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    for i in range(len(values)):
        if i + 1 < length:
            out.append(None)
        else:
            out.append(min(values[i + 1 - length : i + 1]))
    return out


def crossover(prev_a: float, prev_b: float, a: float, b: float) -> bool:
    return a > b and prev_a <= prev_b


def crossunder(prev_a: float, prev_b: float, a: float, b: float) -> bool:
    return a < b and prev_a >= prev_b


def bars_since(series: List[bool]) -> Optional[int]:
    last_index = None
    for i, value in enumerate(series):
        if value:
            last_index = i
    if last_index is None:
        return None
    return len(series) - 1 - last_index


def stoch_rsi(
    values: List[float],
    rsi_len: int,
    stoch_len: int,
    k_len: int,
    d_len: int,
) -> tuple[List[Optional[float]], List[Optional[float]]]:
    rsi_series = rsi(values, rsi_len)
    stoch_values: List[Optional[float]] = [None] * len(values)

    for i in range(len(values)):
        if rsi_series[i] is None:
            continue
        window_start = i + 1 - stoch_len
        if window_start < 0:
            continue
        window = [v for v in rsi_series[window_start : i + 1] if v is not None]
        if not window:
            continue
        low = min(window)
        high = max(window)
        if high == low:
            stoch_values[i] = 0.0
        else:
            stoch_values[i] = 100 * (rsi_series[i] - low) / (high - low)

    k_values = sma([v if v is not None else float("nan") for v in stoch_values], k_len)
    d_values = sma([v if v is not None else float("nan") for v in k_values], d_len)
    return k_values, d_values
