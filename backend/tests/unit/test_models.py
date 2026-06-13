"""Contract + behavioral tests for the multi-tenant ORM schema.

Runs on the lightweight local venv: the models import only SQLAlchemy + stdlib
(no asyncpg/psycopg/app settings), and the behavioral tests use an in-memory
SQLite engine, so no Postgres is required to prove the tenant-scoped dedup key.
"""

from __future__ import annotations

import pytest
from sqlalchemy import UniqueConstraint, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Base,
    Notification,
    NotificationSetting,
    Post,
    Tenant,
    User,
)

pytestmark = pytest.mark.unit

EXPECTED_TABLES = {
    "tenants",
    "users",
    "keywords",
    "monitored_sources",
    "platform_accounts",
    "posts",
    "matches",
    "notifications",
    "notification_settings",
    "scrape_runs",
}


def _unique_column_sets(model) -> list[set[str]]:
    return [
        {c.name for c in con.columns}
        for con in model.__table__.constraints
        if isinstance(con, UniqueConstraint)
    ]


class TestSchemaContract:
    def test_all_expected_tables_present(self):
        assert EXPECTED_TABLES <= set(Base.metadata.tables)

    def test_every_table_except_tenants_carries_tenant_id(self):
        missing = [
            name
            for name, table in Base.metadata.tables.items()
            if name != "tenants" and "tenant_id" not in table.columns
        ]
        assert missing == [], f"tables missing tenant_id: {missing}"

    def test_tenants_table_is_the_tenant_root(self):
        assert "tenant_id" not in Base.metadata.tables["tenants"].columns

    def test_tenant_id_is_non_null_fk_to_tenants(self):
        for name, table in Base.metadata.tables.items():
            if name == "tenants":
                continue
            col = table.columns["tenant_id"]
            assert col.nullable is False, f"{name}.tenant_id must be NOT NULL"
            targets = {fk.column.table.name for fk in col.foreign_keys}
            assert "tenants" in targets, f"{name}.tenant_id must FK to tenants"

    def test_posts_dedup_unique_constraint(self):
        assert {"tenant_id", "platform", "external_id"} in _unique_column_sets(Post)

    def test_posts_has_content_hash(self):
        assert "content_hash" in Post.__table__.columns

    def test_notifications_unique_per_match_and_channel(self):
        assert {"tenant_id", "match_id", "channel"} in _unique_column_sets(Notification)

    def test_notification_settings_unique_per_channel(self):
        assert {"tenant_id", "channel"} in _unique_column_sets(NotificationSetting)

    def test_users_email_unique_per_tenant(self):
        assert {"tenant_id", "email"} in _unique_column_sets(User)


class TestTenantScopedDedup:
    def _fresh_engine(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        return engine

    def test_duplicate_external_id_same_tenant_is_rejected(self):
        engine = self._fresh_engine()
        with Session(engine) as s:
            s.add(Tenant(id=1, name="Acme", slug="acme"))
            s.add(
                Post(
                    id=1,
                    tenant_id=1,
                    platform="facebook",
                    external_id="abc",
                    url="u1",
                    body="b1",
                    content_hash="h1",
                )
            )
            s.commit()
            s.add(
                Post(
                    id=2,
                    tenant_id=1,
                    platform="facebook",
                    external_id="abc",
                    url="u2",
                    body="b2",
                    content_hash="h2",
                )
            )
            with pytest.raises(IntegrityError):
                s.commit()

    def test_same_external_id_across_tenants_is_allowed(self):
        engine = self._fresh_engine()
        with Session(engine) as s:
            s.add(Tenant(id=1, name="Acme", slug="acme"))
            s.add(Tenant(id=2, name="Globex", slug="globex"))
            s.add(
                Post(
                    id=1,
                    tenant_id=1,
                    platform="facebook",
                    external_id="abc",
                    url="u",
                    body="b",
                    content_hash="h",
                )
            )
            s.add(
                Post(
                    id=2,
                    tenant_id=2,
                    platform="facebook",
                    external_id="abc",
                    url="u",
                    body="b",
                    content_hash="h",
                )
            )
            s.commit()
            assert s.query(Post).count() == 2
