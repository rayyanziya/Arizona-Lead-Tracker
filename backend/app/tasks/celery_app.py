"""Celery application: broker, queues, routing, and the beat schedule.

Deliberately free of business logic. The task bodies live in app.tasks.jobs
(loaded via `include`), and the orchestration they call lives in the Celery-free
app.tasks.pipeline / app.tasks.notify -- which is where the unit tests reach it.
Four queues isolate workloads: long/bursty scrapers (reddit, browser) never block
the fast pipeline and notify queues.
"""

from __future__ import annotations

from celery import Celery
from kombu import Queue

from app.core.config import settings

app = Celery(
    "arizona_lead_tracker",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["app.tasks.jobs"],
)

app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,  # fair dispatch: scrapers are long-running
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_default_queue="pipeline",
    task_queues=(
        Queue("pipeline"),
        Queue("notify"),
        Queue("reddit"),
        Queue("browser"),
    ),
    task_routes={
        "app.tasks.jobs.process_post_task": {"queue": "pipeline"},
        "app.tasks.jobs.deliver_task": {"queue": "notify"},
        "app.tasks.jobs.dispatch_due_sources": {"queue": "pipeline"},
        # Phase 3+ platform scrapers route to their isolated queues:
        "app.tasks.jobs.scrape_reddit_source": {"queue": "reddit"},
        "app.tasks.jobs.scrape_browser_source": {"queue": "browser"},
    },
    beat_schedule={
        "dispatch-due-sources": {
            "task": "app.tasks.jobs.dispatch_due_sources",
            "schedule": float(settings.scrape_interval_seconds),
        },
    },
)