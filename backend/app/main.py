"""FastAPI application entrypoint.

Phase 0 shipped a health endpoint only. Phase 6 mounts the dashboard/admin
routers; auth is wired first, with matches/keywords/sources to follow.
"""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import auth, keywords, leads, sources
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


app.include_router(auth.router)
app.include_router(leads.router)
app.include_router(keywords.router)
app.include_router(sources.router)


@app.get("/health", tags=["health"])
async def health() -> dict[str, str]:
    return {"status": "ok", "env": settings.app_env}
