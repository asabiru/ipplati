from contextlib import asynccontextmanager
import threading

from fastapi import FastAPI

from app.api.routes import router
from app.config import settings
from app.db import init_db
from app.services.alfa_auto_sync import alfa_auto_sync_loop
from app.services.auto_sync import auto_sync_loop


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    stop_event = threading.Event()
    workers: list[threading.Thread] = []
    if settings.sber_auto_sync_enabled:
        sber_worker = threading.Thread(target=auto_sync_loop, args=(stop_event,), daemon=True, name="sber-auto-sync")
        sber_worker.start()
        workers.append(sber_worker)
    if settings.alfa_auto_sync_enabled:
        alfa_worker = threading.Thread(target=alfa_auto_sync_loop, args=(stop_event,), daemon=True, name="alfa-auto-sync")
        alfa_worker.start()
        workers.append(alfa_worker)
    yield
    stop_event.set()
    for worker in workers:
        worker.join(timeout=2)


app = FastAPI(title=settings.app_name, lifespan=lifespan)
app.include_router(router)
init_db()
