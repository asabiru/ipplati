from contextlib import asynccontextmanager
import threading

from fastapi import FastAPI

from app.api.routes import router
from app.config import settings
from app.db import init_db
from app.services.auto_sync import auto_sync_loop


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    stop_event = threading.Event()
    worker: threading.Thread | None = None
    if settings.sber_auto_sync_enabled:
        worker = threading.Thread(target=auto_sync_loop, args=(stop_event,), daemon=True, name="sber-auto-sync")
        worker.start()
    yield
    stop_event.set()
    if worker is not None:
        worker.join(timeout=2)


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(router)
init_db()
