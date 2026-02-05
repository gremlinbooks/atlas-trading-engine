from __future__ import annotations

from fastapi import APIRouter

from app.config import get_settings
from app.scheduler import Scheduler

router = APIRouter()


@router.get("/api/v1/health")
async def health() -> dict:
    settings = get_settings()
    scheduler = SchedulerSingleton.instance
    return {
        "status": "ok",
        "last_evaluator_run": scheduler.error_state.get("last_evaluator_run"),
        "last_snapshot_run": scheduler.error_state.get("last_snapshot_run"),
        "symbols": settings.symbols_list,
        "dry_run": settings.dry_run,
        "halted": scheduler.error_state.get("halted", False),
    }


class SchedulerSingleton:
    instance: Scheduler
