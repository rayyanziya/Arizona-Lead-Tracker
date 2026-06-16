"""Application settings, loaded from environment / .env via pydantic-settings."""

from functools import lru_cache

from pydantic import computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore", case_sensitive=False
    )

    # --- App ---
    app_env: str = "development"
    app_debug: bool = True
    app_secret_key: str = "change-me"  # noqa: S105
    app_encryption_key: str = ""  # Fernet key for encrypting stored session blobs

    # --- Postgres ---
    postgres_user: str = "alt"
    postgres_password: str = "alt_password"  # noqa: S105
    postgres_db: str = "arizona_lead_tracker"
    postgres_host: str = "postgres"
    postgres_port: int = 5432

    # --- Redis ---
    redis_host: str = "redis"
    redis_port: int = 6379
    redis_db: int = 0

    # --- Anthropic / Claude ---
    anthropic_api_key: str = ""
    claude_model: str = "claude-haiku-4-5"
    claude_score_threshold: int = 7

    # --- Telegram ---
    telegram_bot_token: str = ""
    telegram_default_chat_id: str = ""

    # --- SMTP ---
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = "leads@arizona-tracker.local"
    smtp_use_tls: bool = False

    # --- Reddit ---
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "arizona-lead-tracker/0.1"

    # --- X (Twitter) ---
    # App-only bearer token for the v2 recent-search endpoint; empty disables the
    # X collector (dispatch logs and skips X sources, like missing Reddit creds).
    x_bearer_token: str = ""

    # --- Browser automation ---
    browser_session_dir: str = "/app/.sessions"
    browser_headless: bool = True
    browser_locale: str = "id-ID"
    browser_timezone: str = "Asia/Jakarta"
    browser_proxy_server: str = ""
    browser_proxy_username: str = ""
    browser_proxy_password: str = ""

    # --- Scrape cadence / anti-ban ---
    scrape_interval_seconds: int = 1200
    scrape_jitter_seconds: int = 600
    scrape_min_delay_ms: int = 2500
    scrape_max_delay_ms: int = 7000
    scrape_max_posts_per_run: int = 40

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url_async(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url_sync(self) -> str:
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/{self.redis_db}"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
