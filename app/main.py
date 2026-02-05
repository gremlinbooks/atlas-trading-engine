from __future__ import annotations

from fastapi import FastAPI

from app.api.control import router as control_router
from app.api.health import SchedulerSingleton, router as health_router
from app.config import get_settings
from app.data.db import init_db
from app.logging.logger import get_logger, log_event
from app.scheduler import Scheduler

system_logger = get_logger("system", "logs/system.jsonl")

app = FastAPI(title="Atlas Trading Engine")
app.include_router(health_router)
app.include_router(control_router)


@app.on_event("startup")
async def startup() -> None:
    init_db()
    scheduler = Scheduler()
    SchedulerSingleton.instance = scheduler
    await scheduler.start()
    settings = get_settings()
    log_event(
        system_logger,
        "startup",
        symbols=settings.symbols_list,
        loop_seconds=settings.loop_seconds,
        snapshot_seconds=settings.snapshot_seconds,
        dry_run=settings.dry_run,
    )
