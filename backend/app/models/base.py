"""Declarative base, shared mixins, and domain enums for the ORM layer.

Importing this module pulls in SQLAlchemy + stdlib only (no DB driver, no app
settings), so the models stay unit-testable on a lightweight interpreter and can
be exercised against an in-memory SQLite engine in tests. ``MatchType`` is
re-used from the keyword matcher so the stored match strategy and the runtime
matcher can never drift apart.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.services.keyword_matcher import MatchType

__all__ = [
    "Base",
    "IdMixin",
    "TimestampMixin",
    "TenantScopedMixin",
    "enum_column",
    "MatchType",
    "Platform",
    "Language",
    "UserRole",
    "AccountStatus",
    "MatchStatus",
    "NotificationChannel",
    "NotificationStatus",
    "ScrapeStatus",
]


class Base(DeclarativeBase):
    """Declarative base; ``Base.metadata`` is the target for Alembic + create_all."""


def enum_column(py_enum: type[enum.Enum]) -> SAEnum:
    """A portable string-backed enum column.

    ``native_enum=False`` renders as VARCHAR + CHECK (works on Postgres *and*
    SQLite, and sidesteps the pain of ALTERing native PG enums later), and
    ``values_callable`` stores the lowercase ``.value`` rather than the member
    name.
    """
    return SAEnum(
        py_enum,
        native_enum=False,
        validate_strings=True,
        values_callable=lambda e: [m.value for m in e],
    )


class Platform(str, enum.Enum):
    FACEBOOK = "facebook"
    REDDIT = "reddit"
    X = "x"


class Language(str, enum.Enum):
    ID = "id"
    EN = "en"
    ANY = "any"


class UserRole(str, enum.Enum):
    OWNER = "owner"
    ADMIN = "admin"
    MEMBER = "member"


class AccountStatus(str, enum.Enum):
    ACTIVE = "active"
    COOLDOWN = "cooldown"
    CHECKPOINT = "checkpoint"
    DISABLED = "disabled"


class MatchStatus(str, enum.Enum):
    PENDING = "pending"
    NOTIFIED = "notified"
    RESPONDED = "responded"
    IGNORED = "ignored"


class NotificationChannel(str, enum.Enum):
    TELEGRAM = "telegram"
    EMAIL = "email"


class NotificationStatus(str, enum.Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class ScrapeStatus(str, enum.Enum):
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    BLOCKED = "blocked"


class IdMixin:
    # BIGINT on Postgres; INTEGER (rowid alias, so it autoincrements) on SQLite.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True
    )


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class TenantScopedMixin:
    """Every business table carries this: the multi-tenancy discriminator."""

    tenant_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("tenants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
