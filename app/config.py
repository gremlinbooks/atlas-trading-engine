from __future__ import annotations

from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    oanda_api_key: str = Field(alias="OANDA_API_KEY")
    oanda_account_id: str = Field(alias="OANDA_ACCOUNT_ID")
    oanda_env: str = Field(alias="OANDA_ENV")

    symbols: str = Field(default="AUD_USD", alias="SYMBOLS")
    loop_seconds: int = Field(default=60, alias="LOOP_SECONDS")
    snapshot_seconds: int = Field(default=300, alias="SNAPSHOT_SECONDS")
    max_spread_pips: float = Field(default=2.0, alias="MAX_SPREAD_PIPS")

    dry_run: bool = Field(default=True, alias="DRY_RUN")
    off_hours_enabled: bool = Field(default=False, alias="OFF_HOURS_ENABLED")
    off_hours_start: Optional[str] = Field(default=None, alias="OFF_HOURS_START")
    off_hours_end: Optional[str] = Field(default=None, alias="OFF_HOURS_END")

    @property
    def symbols_list(self) -> List[str]:
        return [s.strip() for s in self.symbols.split(",") if s.strip()]


def get_settings() -> Settings:
    return Settings()
