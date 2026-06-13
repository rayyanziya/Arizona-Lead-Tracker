"""Celery tasks and the processing pipeline.

This package stays import-light on purpose: ``pipeline`` (the orchestrator) must
import without Celery so it can be unit-tested on a bare venv. The Celery app and
beat schedule live in ``celery_app`` / ``dispatch`` and are imported only by the
worker entrypoints, never by ``pipeline``.
"""