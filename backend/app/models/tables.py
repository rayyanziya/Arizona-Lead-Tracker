"""All ORM table definitions for Arizona Lead Tracker.

Multi-tenant by construction: every table except ``tenants`` mixes in
``TenantScopedMixin`` (a non-null ``tenant_id`` FK), and uniqueness constraints
are tenant-scoped so two tenants never collide on the same external id, keyword,
or source.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import (
    AccountStatus,
    Base,
    IdMixin,
    Language,
    MatchStatus,
    MatchType,
    NotificationChannel,
    NotificationStatus,
    Platform,
    ScrapeStatus,
    TenantScopedMixin,
    TimestampMixin,
    UserRole,
    enum_column,
)


class Tenant(IdMixin, TimestampMixin, Base):
    """A company/workspace. Its ``id`` is the tenant identity everything scopes to."""

    __tablename__ = "tenants"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class User(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),)

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(200))
    role: Mapped[UserRole] = mapped_column(
        enum_column(UserRole), nullable=False, default=UserRole.MEMBER
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Keyword(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    __tablename__ = "keywords"
    __table_args__ = (
        UniqueConstraint("tenant_id", "term", "match_type", name="uq_keywords_tenant_term_type"),
    )

    term: Mapped[str] = mapped_column(String(200), nullable=False)
    language: Mapped[Language] = mapped_column(
        enum_column(Language), nullable=False, default=Language.ANY
    )
    match_type: Mapped[MatchType] = mapped_column(
        enum_column(MatchType), nullable=False, default=MatchType.PHRASE
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class MonitoredSource(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """A thing we watch: a FB group URL, a subreddit, or an X search query."""

    __tablename__ = "monitored_sources"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "platform",
            "identifier",
            name="uq_sources_tenant_platform_identifier",
        ),
    )

    platform: Mapped[Platform] = mapped_column(enum_column(Platform), nullable=False)
    identifier: Mapped[str] = mapped_column(String(500), nullable=False)
    label: Mapped[str | None] = mapped_column(String(200))
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_scraped_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlatformAccount(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """A scraping identity (FB/X login). ``session_blob`` is Fernet-encrypted."""

    __tablename__ = "platform_accounts"

    platform: Mapped[Platform] = mapped_column(enum_column(Platform), nullable=False)
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    username: Mapped[str | None] = mapped_column(String(200))
    session_blob: Mapped[str | None] = mapped_column(Text)
    status: Mapped[AccountStatus] = mapped_column(
        enum_column(AccountStatus), nullable=False, default=AccountStatus.ACTIVE
    )
    cooldown_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Post(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """A collected post. The dedup anchor: UNIQUE(tenant_id, platform, external_id)."""

    __tablename__ = "posts"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "platform",
            "external_id",
            name="uq_posts_tenant_platform_external",
        ),
    )

    platform: Mapped[Platform] = mapped_column(enum_column(Platform), nullable=False)
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("monitored_sources.id", ondelete="SET NULL")
    )
    url: Mapped[str] = mapped_column(Text, nullable=False)
    author: Mapped[str | None] = mapped_column(String(255))
    title: Mapped[str | None] = mapped_column(Text)
    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    match: Mapped[Match | None] = relationship(
        back_populates="post", uselist=False, cascade="all, delete-orphan"
    )


class Match(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """A post that matched keywords and was AI-scored: i.e. a lead."""

    __tablename__ = "matches"
    __table_args__ = (UniqueConstraint("tenant_id", "post_id", name="uq_matches_tenant_post"),)

    post_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("posts.id", ondelete="CASCADE"), nullable=False
    )
    keyword_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("keywords.id", ondelete="SET NULL")
    )
    matched_term: Mapped[str | None] = mapped_column(String(200))
    matched_terms: Mapped[list | None] = mapped_column(JSON)
    ai_score: Mapped[int | None] = mapped_column(Integer)
    ai_is_buyer: Mapped[bool | None] = mapped_column(Boolean)
    ai_reason: Mapped[str | None] = mapped_column(Text)
    status: Mapped[MatchStatus] = mapped_column(
        enum_column(MatchStatus), nullable=False, default=MatchStatus.PENDING
    )

    post: Mapped[Post] = relationship(back_populates="match")
    notifications: Mapped[list[Notification]] = relationship(
        back_populates="match", cascade="all, delete-orphan"
    )


class Notification(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """One dispatch attempt. UNIQUE(tenant_id, match_id, channel) = never twice."""

    __tablename__ = "notifications"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "match_id",
            "channel",
            name="uq_notifications_tenant_match_channel",
        ),
    )

    match_id: Mapped[int] = mapped_column(
        BigInteger, ForeignKey("matches.id", ondelete="CASCADE"), nullable=False
    )
    channel: Mapped[NotificationChannel] = mapped_column(
        enum_column(NotificationChannel), nullable=False
    )
    status: Mapped[NotificationStatus] = mapped_column(
        enum_column(NotificationStatus),
        nullable=False,
        default=NotificationStatus.PENDING,
    )
    target: Mapped[str | None] = mapped_column(String(320))
    error: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    match: Mapped[Match] = relationship(back_populates="notifications")


class NotificationSetting(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Per-tenant channel config + the score threshold (admin panel)."""

    __tablename__ = "notification_settings"
    __table_args__ = (
        UniqueConstraint("tenant_id", "channel", name="uq_notification_settings_tenant_channel"),
    )

    channel: Mapped[NotificationChannel] = mapped_column(
        enum_column(NotificationChannel), nullable=False
    )
    is_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    min_score: Mapped[int] = mapped_column(Integer, nullable=False, default=7)
    config: Mapped[dict | None] = mapped_column(JSON)


class ScrapeRun(IdMixin, TenantScopedMixin, TimestampMixin, Base):
    """Telemetry for one monitor execution (rate-limit + error visibility)."""

    __tablename__ = "scrape_runs"

    source_id: Mapped[int | None] = mapped_column(
        BigInteger, ForeignKey("monitored_sources.id", ondelete="SET NULL")
    )
    platform: Mapped[Platform] = mapped_column(enum_column(Platform), nullable=False)
    status: Mapped[ScrapeStatus] = mapped_column(
        enum_column(ScrapeStatus), nullable=False, default=ScrapeStatus.RUNNING
    )
    posts_collected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    matches_created: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
