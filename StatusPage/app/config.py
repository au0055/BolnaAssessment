"""
Application configuration via Pydantic Settings.

Provider definitions are loaded here.  To add a new provider,
simply append to DEFAULT_PROVIDERS.
"""

from __future__ import annotations

from pydantic_settings import BaseSettings

from app.monitor import ProviderConfig


# ---------------------------------------------------------------------------
# Default provider list — extend this to track more status pages
# ---------------------------------------------------------------------------

DEFAULT_PROVIDERS: list[ProviderConfig] = [
    ProviderConfig(
        name="OpenAI",
        base_url="https://status.openai.com/api/v2",
        poll_interval_seconds=30.0,
    ),
    # Easy to add more providers:
    # ProviderConfig(
    #     name="GitHub",
    #     base_url="https://www.githubstatus.com/api/v2",
    #     poll_interval_seconds=30.0,
    # ),
    # ProviderConfig(
    #     name="Anthropic",
    #     base_url="https://status.anthropic.com/api/v2",
    #     poll_interval_seconds=30.0,
    # ),
]


class AppSettings(BaseSettings):
    """Application-wide settings, overridable via environment variables."""

    app_name: str = "Status Page Tracker"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    poll_interval_override: float | None = None  # Override all provider intervals

    model_config = {"env_prefix": "SPT_"}


settings = AppSettings()
