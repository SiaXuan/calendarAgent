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
from agents.orchestrator import load_health_store, load_task_store
from api.preferences import load_preferences
from api.tasks import router as tasks_router

logger = logging.getLogger("dayflow")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Restore persisted health data so it survives reloads
    load_health_store()
    load_task_store()
    load_preferences()
    # No startup sync — the first /schedule/stream call will sync via the
    # throttle in stream_day_schedule (after yielding the health card, so
    # the user sees immediate feedback while the sync runs).
    pass
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
