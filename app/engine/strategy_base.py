from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class Candle:
    ts: str
    o: float
    h: float
    l: float
    c: float
    volume: int


@dataclass(frozen=True)
class PositionState:
    side: Optional[str]
    units: float
    avg_price: float
    entry_ts: Optional[str]


@dataclass(frozen=True)
class StrategySignal:
    action: str
    reason: str
    metadata: dict[str, Any]


@dataclass
class StrategyConfig:
    timeframe: str
    min_hold_bars: int
    trend_ema_period: int
    enabled: bool
    fast_len: int = 3
    slow_len: int = 4
    use_bias: bool = False
    invert_eurcad: bool = True
    force_flip: bool = True
    tp1_pips: int = 20
    sl_pips: int = 28
    tp1_close_pct: int = 30
    trail_drawdown_pct: float = 2.0
    be_lock_pips: int = 20
    stoch_entry_mode: str = "Off"
    use_stoch_exit: bool = True
    st_rsi_len: int = 12
    st_stoch_len: int = 16
    st_k_len: int = 3
    st_d_len: int = 9
    st_ob: float = 89.0
    st_os: float = 14.0
    st_recent: int = 0
    st_tight_pips: int = 6
    block_trades: bool = False
    block_session: str = "1500-2200"
    quick_relax: bool = False
    use_day_mask: bool = False
    block_mon: bool = False
    block_tue: bool = False
    block_wed: bool = False
    block_thu: bool = False
    block_fri: bool = False
    block_sat: bool = False
    block_sun: bool = False
    use_spread_gate: bool = True
    max_spread_pips: float = 5.0
    aggr_spread_factor: float = 1.0
    hold_signal_bars: int = 8
    apply_on_history: bool = False
    pb_enabled: bool = True
    pb_lookback_bars: int = 8
    cont_enabled: bool = True
    base_max_bars: int = 5
    base_max_range_atr: float = 0.6
    allow_second_chance: bool = True
    reenter_within_bars: int = 12


class Strategy(ABC):
    @abstractmethod
    def evaluate(
        self,
        candles: list[Candle],
        ctx: "StrategyContext",
    ) -> "StrategyDecision":
        raise NotImplementedError


def get_strategy(name: str) -> Strategy:
    if name == "tv_port_v1":
        from app.engine.strategies.tv_port_v1 import TVPortV1Strategy

        return TVPortV1Strategy()
    if name == "oakbridge_fxtrader_v2":
        from app.engine.strategies.oakbridge_fxtrader_v2 import OakBridgeFxTraderV2

        return OakBridgeFxTraderV2()
    raise ValueError(f"Unknown strategy: {name}")


@dataclass(frozen=True)
class StrategyState:
    long_tp1_reached: bool = False
    short_tp1_reached: bool = False
    long_tp1_done: bool = False
    short_tp1_done: bool = False
    long_peak: Optional[float] = None
    short_trough: Optional[float] = None
    last_trade_candle_ts: Optional[str] = None
    last_trade_index: Optional[int] = None
    last_flat_index: Optional[int] = None
    last_long_intent_index: Optional[int] = None
    last_short_intent_index: Optional[int] = None
    pend_long: bool = False
    pend_short: bool = False
    pend_long_age: int = 0
    pend_short_age: int = 0


@dataclass(frozen=True)
class StrategyContext:
    symbol: str
    timeframe: str
    position: PositionState
    config: StrategyConfig
    state: StrategyState
    bar_index: int
    spread_pips: Optional[float] = None
    spread_available: bool = False
    is_realtime: bool = False


@dataclass(frozen=True)
class StrategyDecision:
    action: str
    reason: str
    metadata: dict[str, Any]
    price: Optional[float] = None
    units: Optional[float] = None
    next_state: Optional[StrategyState] = None
