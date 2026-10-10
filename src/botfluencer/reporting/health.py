import time

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from botfluencer.runtime.worker import Worker


def create_app(worker: Worker) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/health/live")
    async def live() -> dict[str, str]:
        return {"status": "alive"}

    @app.get("/health/ready")
    async def ready() -> JSONResponse:
        state = worker.state
        healthy = state.initialized and state.role in {"active", "standby"}
        if state.role == "active":
            healthy = (
                healthy
                and state.heartbeat_monotonic is not None
                and time.monotonic() - state.heartbeat_monotonic
                < worker.settings.heartbeat_timeout_seconds
            )
        try:
            await worker.repo.db.ping()
        except Exception:
            healthy = False
        return JSONResponse(
            {"status": "ready" if healthy else "not_ready", "role": state.role},
            status_code=200 if healthy else 503,
        )

    @app.get("/health/worker")
    async def worker_health() -> JSONResponse:
        state = worker.state
        age = (
            time.monotonic() - state.heartbeat_monotonic
            if state.heartbeat_monotonic is not None
            else None
        )
        healthy = (
            state.role == "active"
            and age is not None
            and age < worker.settings.heartbeat_timeout_seconds
        )
        try:
            await worker.repo.db.ping()
        except Exception:
            healthy = False
        return JSONResponse(
            {
                "status": "healthy" if healthy else "unhealthy",
                "role": state.role,
                "paused": state.paused,
                "heartbeat_age_seconds": round(age, 2) if age is not None else None,
            },
            status_code=200 if healthy else 503,
        )

    return app
