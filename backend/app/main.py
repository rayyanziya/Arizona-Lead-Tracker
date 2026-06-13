"""FastAPI application entrypoint.

Phase 0 ships a health endpoint only. Phase 6 mounts the dashboard/admin routers
(matches, keywords, sources, settings, auth).
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.logging import configure_logging

configure_logging()

app = FastAPI(title="Arizona Lead Tracker", version="0.1.0", debug=settings.app_debug)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Phase 7: tighten to the dashboard origin per environment.
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.app_env}
