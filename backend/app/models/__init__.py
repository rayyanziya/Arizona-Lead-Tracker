"""Multi-tenant ORM models.

Importing this package registers every table on ``Base.metadata`` (Alembic and
``create_all`` rely on that import side effect) and re-exports the declarative
base, shared mixins, and domain enums.
"""

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
from app.models.tables import (
    Keyword,
    Match,
    MonitoredSource,
    Notification,
    NotificationSetting,
    PlatformAccount,
    Post,
    ScrapeRun,
    Tenant,
    User,
)

__all__ = [
    "Base",
    "IdMixin",
    "TenantScopedMixin",
    "TimestampMixin",
    "enum_column",
    "AccountStatus",
    "Language",
    "MatchStatus",
    "MatchType",
    "NotificationChannel",
    "NotificationStatus",
    "Platform",
    "ScrapeStatus",
    "UserRole",
    "Keyword",
    "Match",
    "MonitoredSource",
    "Notification",
    "NotificationSetting",
    "PlatformAccount",
    "Post",
    "ScrapeRun",
    "Tenant",
    "User",
]
