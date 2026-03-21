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
    margin_usage_pct: float = Field(default=0.0, alias="MARGIN_USAGE_PCT")
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
    strategy_max_hold_bars: int = Field(default=0, alias="STRATEGY_MAX_HOLD_BARS")
    strategy_drawdown_stop_pips: float = Field(default=15.0, alias="STRATEGY_DRAWDOWN_STOP_PIPS")
    strategy_drawdown_stop_bars: int = Field(default=0, alias="STRATEGY_DRAWDOWN_STOP_BARS")
    strategy_tp1_close_pct: int = Field(default=30, alias="STRATEGY_TP1_CLOSE_PCT")
    strategy_trail_drawdown_pct: float = Field(default=2.0, alias="STRATEGY_TRAIL_DRAWDOWN_PCT")
    strategy_be_lock_pips: int = Field(default=20, alias="STRATEGY_BE_LOCK_PIPS")
    strategy_profit_floor1_trigger_pips: int = Field(default=10, alias="STRATEGY_PROFIT_FLOOR1_TRIGGER_PIPS")
    strategy_profit_floor1_lock_pips: int = Field(default=10, alias="STRATEGY_PROFIT_FLOOR1_LOCK_PIPS")
    strategy_profit_floor2_trigger_pips: int = Field(default=15, alias="STRATEGY_PROFIT_FLOOR2_TRIGGER_PIPS")
    strategy_profit_floor2_lock_pips: int = Field(default=15, alias="STRATEGY_PROFIT_FLOOR2_LOCK_PIPS")
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
    strategy_block_entry_hours_utc: str = Field(default="", alias="STRATEGY_BLOCK_ENTRY_HOURS_UTC")
    strategy_no_intent_override_enabled: bool = Field(default=False, alias="STRATEGY_NO_INTENT_OVERRIDE_ENABLED")
    strategy_no_intent_override_hours_utc: str = Field(default="6,7,11", alias="STRATEGY_NO_INTENT_OVERRIDE_HOURS_UTC")
    strategy_no_intent_override_atr_mult: float = Field(default=1.6, alias="STRATEGY_NO_INTENT_OVERRIDE_ATR_MULT")
    strategy_no_intent_override_body_ratio_min: float = Field(
        default=0.7, alias="STRATEGY_NO_INTENT_OVERRIDE_BODY_RATIO_MIN"
    )
    strategy_no_intent_override_close_extreme_frac: float = Field(
        default=0.2, alias="STRATEGY_NO_INTENT_OVERRIDE_CLOSE_EXTREME_FRAC"
    )
    strategy_no_intent_override_volume_lookback: int = Field(
        default=20, alias="STRATEGY_NO_INTENT_OVERRIDE_VOLUME_LOOKBACK"
    )
    strategy_no_intent_override_volume_percentile: float = Field(
        default=65.0, alias="STRATEGY_NO_INTENT_OVERRIDE_VOLUME_PERCENTILE"
    )
    strategy_no_intent_override_risk_scale: float = Field(
        default=0.6, alias="STRATEGY_NO_INTENT_OVERRIDE_RISK_SCALE"
    )
    strategy_hour_strict_mode_enabled: bool = Field(default=False, alias="STRATEGY_HOUR_STRICT_MODE_ENABLED")
    strategy_hour_strict_hours_utc: str = Field(default="6,7,11,20", alias="STRATEGY_HOUR_STRICT_HOURS_UTC")
    strategy_hour_strict_require_cross_or_continuation: bool = Field(
        default=True, alias="STRATEGY_HOUR_STRICT_REQUIRE_CROSS_OR_CONTINUATION"
    )
    strategy_hour_strict_risk_scale: float = Field(default=0.8, alias="STRATEGY_HOUR_STRICT_RISK_SCALE")
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
    strategy_pb_enabled_long: bool = Field(default=True, alias="STRATEGY_PB_ENABLED_LONG")
    strategy_pb_enabled_short: bool = Field(default=True, alias="STRATEGY_PB_ENABLED_SHORT")
    strategy_pb_lookback_bars: int = Field(default=8, alias="STRATEGY_PB_LOOKBACK_BARS")
    strategy_cont_enabled: bool = Field(default=True, alias="STRATEGY_CONT_ENABLED")
    strategy_base_max_bars: int = Field(default=5, alias="STRATEGY_BASE_MAX_BARS")
    strategy_base_max_range_atr: float = Field(default=0.6, alias="STRATEGY_BASE_MAX_RANGE_ATR")
    strategy_rejoin_enabled: bool = Field(default=True, alias="STRATEGY_REJOIN_ENABLED")
    strategy_rejoin_enabled_long: bool = Field(default=True, alias="STRATEGY_REJOIN_ENABLED_LONG")
    strategy_rejoin_enabled_short: bool = Field(default=True, alias="STRATEGY_REJOIN_ENABLED_SHORT")
    strategy_allow_second_chance: bool = Field(default=True, alias="STRATEGY_ALLOW_SECOND_CHANCE")
    strategy_reenter_within_bars: int = Field(default=12, alias="STRATEGY_REENTER_WITHIN_BARS")
    strategy_early_loss_cut_pips: float = Field(default=0.0, alias="STRATEGY_EARLY_LOSS_CUT_PIPS")
    strategy_momentum_fail_exit_pips: float = Field(default=0.0, alias="STRATEGY_MOMENTUM_FAIL_EXIT_PIPS")
    strategy_intrabar_loss_exit_enabled: bool = Field(
        default=False, alias="STRATEGY_INTRABAR_LOSS_EXIT_ENABLED"
    )
    strategy_intrabar_loss_exit_pips: float = Field(default=28.0, alias="STRATEGY_INTRABAR_LOSS_EXIT_PIPS")
    strategy_exit_inspect_tf: str = Field(default="", alias="STRATEGY_EXIT_INSPECT_TF")
    strategy_exit_inspect_candle_count: int = Field(default=200, alias="STRATEGY_EXIT_INSPECT_CANDLE_COUNT")

    dry_run: bool = Field(default=True, alias="DRY_RUN")
    off_hours_enabled: bool = Field(default=False, alias="OFF_HOURS_ENABLED")
    off_hours_start: Optional[str] = Field(default=None, alias="OFF_HOURS_START")
    off_hours_end: Optional[str] = Field(default=None, alias="OFF_HOURS_END")

    @property
    def symbols_list(self) -> List[str]:
        return [s.strip() for s in self.symbols.split(",") if s.strip()]

    @model_validator(mode="after")
    def _validate_oanda(self) -> "Settings":
        if not (0.0 <= self.margin_usage_pct <= 100.0):
            raise ValueError("MARGIN_USAGE_PCT must be between 0 and 100")
        if self.force_signal:
            forced = self.force_signal.upper()
            if forced not in {"LONG", "SHORT", "HOLD"}:
                raise ValueError("FORCE_SIGNAL must be LONG, SHORT, HOLD, or empty")
        if self.strategy_block_entry_hours_utc.strip():
            tokens = [t.strip() for t in self.strategy_block_entry_hours_utc.split(",") if t.strip()]
            for token in tokens:
                if not token.isdigit():
                    raise ValueError(
                        "STRATEGY_BLOCK_ENTRY_HOURS_UTC must be a comma-separated list of UTC hours (0-23)"
                    )
                hour = int(token)
                if hour < 0 or hour > 23:
                    raise ValueError(
                        "STRATEGY_BLOCK_ENTRY_HOURS_UTC must be a comma-separated list of UTC hours (0-23)"
                    )
        if self.strategy_no_intent_override_hours_utc.strip():
            tokens = [t.strip() for t in self.strategy_no_intent_override_hours_utc.split(",") if t.strip()]
            for token in tokens:
                if not token.isdigit():
                    raise ValueError(
                        "STRATEGY_NO_INTENT_OVERRIDE_HOURS_UTC must be a comma-separated list of UTC hours (0-23)"
                    )
                hour = int(token)
                if hour < 0 or hour > 23:
                    raise ValueError(
                        "STRATEGY_NO_INTENT_OVERRIDE_HOURS_UTC must be a comma-separated list of UTC hours (0-23)"
                    )
        if self.strategy_no_intent_override_atr_mult <= 0:
            raise ValueError("STRATEGY_NO_INTENT_OVERRIDE_ATR_MULT must be > 0")
        if not (0.0 <= self.strategy_no_intent_override_body_ratio_min <= 1.0):
            raise ValueError("STRATEGY_NO_INTENT_OVERRIDE_BODY_RATIO_MIN must be between 0 and 1")
        if not (0.0 <= self.strategy_no_intent_override_close_extreme_frac <= 1.0):
            raise ValueError("STRATEGY_NO_INTENT_OVERRIDE_CLOSE_EXTREME_FRAC must be between 0 and 1")
        if self.strategy_no_intent_override_volume_lookback < 5:
            raise ValueError("STRATEGY_NO_INTENT_OVERRIDE_VOLUME_LOOKBACK must be >= 5")
        if not (0.0 <= self.strategy_no_intent_override_volume_percentile <= 100.0):
            raise ValueError("STRATEGY_NO_INTENT_OVERRIDE_VOLUME_PERCENTILE must be between 0 and 100")
        if not (0.0 < self.strategy_no_intent_override_risk_scale <= 1.0):
            raise ValueError("STRATEGY_NO_INTENT_OVERRIDE_RISK_SCALE must be > 0 and <= 1")
        if self.strategy_hour_strict_hours_utc.strip():
            tokens = [t.strip() for t in self.strategy_hour_strict_hours_utc.split(",") if t.strip()]
            for token in tokens:
                if not token.isdigit():
                    raise ValueError(
                        "STRATEGY_HOUR_STRICT_HOURS_UTC must be a comma-separated list of UTC hours (0-23)"
                    )
                hour = int(token)
                if hour < 0 or hour > 23:
                    raise ValueError(
                        "STRATEGY_HOUR_STRICT_HOURS_UTC must be a comma-separated list of UTC hours (0-23)"
                    )
        if not (0.0 < self.strategy_hour_strict_risk_scale <= 1.0):
            raise ValueError("STRATEGY_HOUR_STRICT_RISK_SCALE must be > 0 and <= 1")
        if self.strategy_intrabar_loss_exit_pips <= 0:
            raise ValueError("STRATEGY_INTRABAR_LOSS_EXIT_PIPS must be > 0")
        if self.dry_run:
            return self
        if not self.oanda_api_key or not self.oanda_account_id or not self.oanda_env:
            raise ValueError("OANDA credentials must be set when DRY_RUN=false")
        if self.oanda_env not in {"practice", "live"}:
            raise ValueError("OANDA_ENV must be practice or live")
        return self


def get_settings() -> Settings:
    return Settings()
