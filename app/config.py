"""Configuration loaded from the environment.

The only secret is REMOTE_API_TOKEN, which must never leave the environment
(no logging, no disk, no crossing the API route boundary). All other values
are non-secret operational tunables with defaults verified during discovery.
"""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Secret. Sandbox tokens are prefixed ra_test_. Sent as Bearer auth.
    remote_api_token: str = Field(default="", alias="REMOTE_API_TOKEN")

    # Verified during discovery: NOT gateway.remote.com, and no /api prefix.
    remote_api_base_url: str = "https://gateway.remote-sandbox.com"

    # Fetch strategy tunables (see CLAUDE.md "Data fetch strategy").
    list_page_size: int = 100
    max_concurrent_requests: int = 8
    max_retries: int = 3
    request_timeout_seconds: float = 30.0

    # In-memory cache TTL for the fully built org chart.
    cache_ttl_seconds: int = 15 * 60

    @property
    def token_is_sandbox(self) -> bool:
        return self.remote_api_token.startswith("ra_test_")


@lru_cache
def get_settings() -> Settings:
    """Cached singleton so the .env file is read once per process."""
    return Settings()
