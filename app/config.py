from __future__ import annotations

from typing import List, Optional

from pydantic import Field
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    oanda_api_key: str = Field(default="", alias="OANDA_API_KEY")
    oanda_account_id: str = Field(default="", alias="OANDA_ACCOUNT_ID")
    oanda_env: str = Field(default="", alias="OANDA_ENV")

    symbols: str = Field(default="AUD_USD", alias="SYMBOLS")
    loop_seconds: int = Field(default=60, alias="LOOP_SECONDS")
    snapshot_seconds: int = Field(default=300, alias="SNAPSHOT_SECONDS")
    max_spread_pips: float = Field(default=2.0, alias="MAX_SPREAD_PIPS")
    timeframe: str = Field(default="M15", alias="TIMEFRAME")
    candle_count: int = Field(default=200, alias="CANDLE_COUNT")
    candle_poll_seconds: int = Field(default=30, alias="CANDLE_POLL_SECONDS")
    default_units: int = Field(default=1000, alias="DEFAULT_UNITS")
    force_signal: Optional[str] = Field(default=None, alias="FORCE_SIGNAL")
    strategy_name: str = Field(default="oakbridge_fxtrader_v2", alias="STRATEGY_NAME")
    strategy_enabled: bool = Field(default=False, alias="STRATEGY_ENABLED")
    strategy_min_hold_bars: int = Field(default=3, alias="STRATEGY_MIN_HOLD_BARS")
    strategy_trend_ema_period: int = Field(default=50, alias="STRATEGY_TREND_EMA_PERIOD")
    strategy_fast_len: int = Field(default=3, alias="STRATEGY_FAST_LEN")
    strategy_slow_len: int = Field(default=4, alias="STRATEGY_SLOW_LEN")
    strategy_use_bias: bool = Field(default=False, alias="STRATEGY_USE_BIAS")
    strategy_invert_eurcad: bool = Field(default=True, alias="STRATEGY_INVERT_EURCAD")
    strategy_force_flip: bool = Field(default=True, alias="STRATEGY_FORCE_FLIP")
    strategy_tp1_pips: int = Field(default=20, alias="STRATEGY_TP1_PIPS")
    strategy_sl_pips: int = Field(default=28, alias="STRATEGY_SL_PIPS")
    strategy_tp1_close_pct: int = Field(default=30, alias="STRATEGY_TP1_CLOSE_PCT")
    strategy_trail_drawdown_pct: float = Field(default=2.0, alias="STRATEGY_TRAIL_DRAWDOWN_PCT")
    strategy_be_lock_pips: int = Field(default=20, alias="STRATEGY_BE_LOCK_PIPS")
    strategy_stoch_entry_mode: str = Field(default="Off", alias="STRATEGY_STOCH_ENTRY_MODE")
    strategy_use_stoch_exit: bool = Field(default=True, alias="STRATEGY_USE_STOCH_EXIT")
    strategy_st_rsi_len: int = Field(default=12, alias="STRATEGY_ST_RSI_LEN")
    strategy_st_stoch_len: int = Field(default=16, alias="STRATEGY_ST_STOCH_LEN")
    strategy_st_k_len: int = Field(default=3, alias="STRATEGY_ST_K_LEN")
    strategy_st_d_len: int = Field(default=9, alias="STRATEGY_ST_D_LEN")
    strategy_st_ob: float = Field(default=89.0, alias="STRATEGY_ST_OB")
    strategy_st_os: float = Field(default=14.0, alias="STRATEGY_ST_OS")
    strategy_st_recent: int = Field(default=0, alias="STRATEGY_ST_RECENT")
    strategy_st_tight_pips: int = Field(default=6, alias="STRATEGY_ST_TIGHT_PIPS")
    strategy_block_trades: bool = Field(default=False, alias="STRATEGY_BLOCK_TRADES")
    strategy_block_session: str = Field(default="1500-2200", alias="STRATEGY_BLOCK_SESSION")
    strategy_quick_relax: bool = Field(default=False, alias="STRATEGY_QUICK_RELAX")
    strategy_use_day_mask: bool = Field(default=False, alias="STRATEGY_USE_DAY_MASK")
    strategy_block_mon: bool = Field(default=False, alias="STRATEGY_BLOCK_MON")
    strategy_block_tue: bool = Field(default=False, alias="STRATEGY_BLOCK_TUE")
    strategy_block_wed: bool = Field(default=False, alias="STRATEGY_BLOCK_WED")
    strategy_block_thu: bool = Field(default=False, alias="STRATEGY_BLOCK_THU")
    strategy_block_fri: bool = Field(default=False, alias="STRATEGY_BLOCK_FRI")
    strategy_block_sat: bool = Field(default=False, alias="STRATEGY_BLOCK_SAT")
    strategy_block_sun: bool = Field(default=False, alias="STRATEGY_BLOCK_SUN")
    strategy_use_spread_gate: bool = Field(default=True, alias="STRATEGY_USE_SPREAD_GATE")
    strategy_max_spread_pips: float = Field(default=5.0, alias="STRATEGY_MAX_SPREAD_PIPS")
    strategy_aggr_spread_factor: float = Field(default=1.0, alias="STRATEGY_AGGR_SPREAD_FACTOR")
    strategy_hold_signal_bars: int = Field(default=8, alias="STRATEGY_HOLD_SIGNAL_BARS")
    strategy_apply_on_history: bool = Field(default=False, alias="STRATEGY_APPLY_ON_HISTORY")
    strategy_pb_enabled: bool = Field(default=True, alias="STRATEGY_PB_ENABLED")
    strategy_pb_lookback_bars: int = Field(default=8, alias="STRATEGY_PB_LOOKBACK_BARS")
    strategy_cont_enabled: bool = Field(default=True, alias="STRATEGY_CONT_ENABLED")
    strategy_base_max_bars: int = Field(default=5, alias="STRATEGY_BASE_MAX_BARS")
    strategy_base_max_range_atr: float = Field(default=0.6, alias="STRATEGY_BASE_MAX_RANGE_ATR")
    strategy_allow_second_chance: bool = Field(default=True, alias="STRATEGY_ALLOW_SECOND_CHANCE")
    strategy_reenter_within_bars: int = Field(default=12, alias="STRATEGY_REENTER_WITHIN_BARS")

    dry_run: bool = Field(default=True, alias="DRY_RUN")
    off_hours_enabled: bool = Field(default=False, alias="OFF_HOURS_ENABLED")
    off_hours_start: Optional[str] = Field(default=None, alias="OFF_HOURS_START")
    off_hours_end: Optional[str] = Field(default=None, alias="OFF_HOURS_END")

    @property
    def symbols_list(self) -> List[str]:
        return [s.strip() for s in self.symbols.split(",") if s.strip()]

    @model_validator(mode="after")
    def _validate_oanda(self) -> "Settings":
        if self.force_signal:
            forced = self.force_signal.upper()
            if forced not in {"LONG", "SHORT", "HOLD"}:
                raise ValueError("FORCE_SIGNAL must be LONG, SHORT, HOLD, or empty")
        if self.dry_run:
            return self
        if not self.oanda_api_key or not self.oanda_account_id or not self.oanda_env:
            raise ValueError("OANDA credentials must be set when DRY_RUN=false")
        if self.oanda_env not in {"practice", "live"}:
            raise ValueError("OANDA_ENV must be practice or live")
        return self


def get_settings() -> Settings:
    return Settings()
