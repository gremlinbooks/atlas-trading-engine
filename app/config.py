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
