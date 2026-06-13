"""Database seed: a pure, inspectable plan + an idempotent writer.

``build_seed_plan()`` returns plain dataclasses (no ORM, no DB) so it can be
unit-tested anywhere. ``seed(session)`` materializes the plan, getting-or-creating
by natural keys so re-running never duplicates. Heavy imports (ORM, password
hashing, DB engine) are deferred so importing this module stays cheap.

Run inside the API/worker container:  python -m scripts.seed
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class KeywordSeed:
    term: str
    language: str
    match_type: str = "phrase"


@dataclass(frozen=True)
class SourceSeed:
    platform: str
    identifier: str
    label: str


@dataclass(frozen=True)
class NotificationSeed:
    channel: str
    is_enabled: bool
    min_score: int
    config: dict


@dataclass(frozen=True)
class SeedPlan:
    tenant_name: str
    tenant_slug: str
    user_email: str
    user_password: str
    user_full_name: str
    keywords: list[KeywordSeed]
    sources: list[SourceSeed]
    notifications: list[NotificationSeed]


def build_seed_plan() -> SeedPlan:
    """The default development tenant: keywords (ID+EN), one FB group, channels."""
    keywords = [
        # --- Bahasa Indonesia (buyer intent) ---
        KeywordSeed("butuh aplikasi", "id"),
        KeywordSeed("jasa pembuatan aplikasi", "id"),
        KeywordSeed("jasa bikin website", "id"),
        KeywordSeed("cari developer", "id"),
        KeywordSeed("bikin sistem", "id"),
        KeywordSeed("aplikasi kasir", "id"),
        KeywordSeed("sistem inventory", "id"),
        KeywordSeed("aplikasi absensi", "id"),
        # --- English (buyer intent) ---
        KeywordSeed("looking for a developer", "en"),
        KeywordSeed("need a custom app", "en"),
        KeywordSeed("build a crm", "en"),
        KeywordSeed("custom software", "en"),
        # --- Acronyms: whole-word, case-insensitive, language-agnostic ---
        KeywordSeed("erp", "any", "exact"),
        KeywordSeed("hris", "any", "exact"),
        KeywordSeed("pos system", "any", "exact"),
    ]
    sources = [
        SourceSeed(
            "facebook",
            "https://www.facebook.com/groups/REPLACE_WITH_GROUP_ID",
            "Example UMKM / Business Group",
        ),
    ]
    notifications = [
        NotificationSeed("telegram", True, 7, {"chat_id": ""}),
        NotificationSeed("email", True, 7, {"to": ["leads@arizona-tracker.local"]}),
    ]
    return SeedPlan(
        tenant_name="Arizona Lead Tracker",
        tenant_slug="arizona",
        user_email="admin@arizona-tracker.local",
        user_password="changeme123",
        user_full_name="Arizona Admin",
        keywords=keywords,
        sources=sources,
        notifications=notifications,
    )


def seed(session, hasher: Callable[[str], str] | None = None) -> dict[str, int]:
    """Idempotently materialize the seed plan. Returns a count of created rows."""
    from app.models import (
        Keyword,
        MonitoredSource,
        NotificationSetting,
        Tenant,
        User,
    )

    if hasher is None:
        from app.core.security import hash_password

        hasher = hash_password

    plan = build_seed_plan()
    created = {
        "tenants": 0,
        "users": 0,
        "keywords": 0,
        "sources": 0,
        "notification_settings": 0,
    }

    tenant = session.query(Tenant).filter_by(slug=plan.tenant_slug).one_or_none()
    if tenant is None:
        tenant = Tenant(name=plan.tenant_name, slug=plan.tenant_slug, is_active=True)
        session.add(tenant)
        session.flush()  # assign tenant.id for the FKs below
        created["tenants"] += 1

    existing_user = (
        session.query(User).filter_by(tenant_id=tenant.id, email=plan.user_email).one_or_none()
    )
    if existing_user is None:
        session.add(
            User(
                tenant_id=tenant.id,
                email=plan.user_email,
                hashed_password=hasher(plan.user_password),
                full_name=plan.user_full_name,
                role="owner",
                is_active=True,
            )
        )
        created["users"] += 1

    for kw in plan.keywords:
        exists = (
            session.query(Keyword)
            .filter_by(tenant_id=tenant.id, term=kw.term, match_type=kw.match_type)
            .one_or_none()
        )
        if exists is None:
            session.add(
                Keyword(
                    tenant_id=tenant.id,
                    term=kw.term,
                    language=kw.language,
                    match_type=kw.match_type,
                    is_active=True,
                )
            )
            created["keywords"] += 1

    for src in plan.sources:
        exists = (
            session.query(MonitoredSource)
            .filter_by(tenant_id=tenant.id, platform=src.platform, identifier=src.identifier)
            .one_or_none()
        )
        if exists is None:
            session.add(
                MonitoredSource(
                    tenant_id=tenant.id,
                    platform=src.platform,
                    identifier=src.identifier,
                    label=src.label,
                    is_active=True,
                )
            )
            created["sources"] += 1

    for note in plan.notifications:
        exists = (
            session.query(NotificationSetting)
            .filter_by(tenant_id=tenant.id, channel=note.channel)
            .one_or_none()
        )
        if exists is None:
            session.add(
                NotificationSetting(
                    tenant_id=tenant.id,
                    channel=note.channel,
                    is_enabled=note.is_enabled,
                    min_score=note.min_score,
                    config=note.config,
                )
            )
            created["notification_settings"] += 1

    return created


def main() -> None:
    from app.core.database import session_scope

    with session_scope() as session:
        created = seed(session)
    print("Seed complete:", created)


if __name__ == "__main__":
    main()
