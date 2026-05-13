from __future__ import annotations

# 允许在 `backend/app` 下直接 `python main.py`：将 `backend/` 加入 sys.path，保证 `import app.*` 可用
import sys
from pathlib import Path as _Path

_BACKEND_ROOT = _Path(__file__).resolve().parent.parent
if str(_BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(_BACKEND_ROOT))

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware

from app.api.session_routes import router as session_router
from app.api.ws import scheduler_websocket_loop
from app.config import settings
from app.services.idle_queue_processor import idle_queue_processor_loop

_env_file = _BACKEND_ROOT / ".env"
if _env_file.is_file():
    load_dotenv(_env_file)
else:
    load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.graphs.runtime import bootstrap_graphs

    await bootstrap_graphs()
    stop = asyncio.Event()
    task = asyncio.create_task(idle_queue_processor_loop(stop), name="idle_queue_processor")
    logger.info(
        "idle queue processor started (enabled=%s interval=%ss)",
        settings.idle_queue_processor_enabled,
        settings.idle_queue_poll_interval,
    )
    yield
    stop.set()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    logger.info("idle queue processor stopped")


app = FastAPI(title="multi-coach-agent", lifespan=lifespan)

app.include_router(session_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"ok": True, "data_dir": str(Path(settings.data_dir).resolve())}


@app.websocket("/ws/{user_id}")
async def ws_scheduler(user_id: str, websocket: WebSocket):
    await scheduler_websocket_loop(websocket, user_id=user_id)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=False,
    )
