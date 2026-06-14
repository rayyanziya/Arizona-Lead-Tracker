"""Dashboard/admin HTTP API (FastAPI routers).

Thin transport over the same services the Celery side uses. Routers are mounted
by app.main; cross-cutting auth lives in app.api.deps.
"""