import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()

from api.chat import router as chat_router
from api.health import router as health_router
from api.preferences import router as preferences_router
from api.schedule import router as schedule_router
from api.task_chat import router as task_chat_router
from agents.orchestrator import load_health_store
from api.tasks import do_sync_reminders, router as tasks_router

logger = logging.getLogger("dayflow")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Restore persisted health data so it survives reloads
    load_health_store()
    # Auto-sync reminders on every startup so the task store is never empty
    try:
        result = await do_sync_reminders()
        logger.info(
            "Startup sync: added=%d updated=%d skipped=%d",
            result["added"], result["updated"], result["skipped"],
        )
    except Exception as exc:
        logger.warning("Startup reminder sync failed (non-fatal): %s", exc)
    yield


app = FastAPI(
    title="Health-Aware AI Scheduling Agent",
    description="Multi-agent LLM scheduler integrating biometric data with calendar and tasks.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router, tags=["health"])
app.include_router(schedule_router, tags=["schedule"])
app.include_router(tasks_router, tags=["tasks"])
app.include_router(chat_router, tags=["chat"])
app.include_router(task_chat_router, tags=["task-chat"])
app.include_router(preferences_router, tags=["preferences"])


@app.get("/")
async def root():
    return {"status": "ok", "message": "Health-Aware Scheduling Agent running."}
