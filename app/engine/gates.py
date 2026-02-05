from __future__ import annotations

from datetime import datetime, time, timezone

from app.config import get_settings


def is_off_hours() -> bool:
    settings = get_settings()
    if not settings.off_hours_enabled:
        return False
    if not settings.off_hours_start or not settings.off_hours_end:
        return False

    start = _parse_time(settings.off_hours_start)
    end = _parse_time(settings.off_hours_end)
    now_utc = datetime.now(timezone.utc).time()

    if start <= end:
        return start <= now_utc <= end
    return now_utc >= start or now_utc <= end


def is_spread_ok(spread_pips: float) -> bool:
    settings = get_settings()
    return spread_pips <= settings.max_spread_pips


def _parse_time(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))
