"""Application settings, loaded from environment variables.

Every setting can be overridden with an ``RELAY_`` prefixed variable,
e.g. ``RELAY_PRIMARY_MODEL``. The API key is read from the standard
``ANTHROPIC_API_KEY`` variable so it can come from a secret store.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import AliasChoices, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# Public list prices (USD per million tokens). Override with
# RELAY_PRICES='{"model-id": {"input": 1.0, "output": 5.0}}'.
DEFAULT_PRICES: dict[str, dict[str, float]] = {
    "claude-opus-5-5": {"input": 4.0, "output": 20.0},
    "claude-sonnet-5-5": {"input": 2.0, "output": 10.0},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50},
    "mock": {"input": 0.0, "output": 0.0},
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RELAY_", env_file=".env", extra="ignore")

    app_name: str = "Relay"
    environment: str = "local"
    log_level: str = "INFO"

    # LLM
    anthropic_api_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "RELAY_ANTHROPIC_API_KEY"),
    )
    primary_model: str = "claude-sonnet-5-5"
    fallback_model: str | None = "claude-haiku-5-5"
    max_tokens: int = Field(default=1024, ge=64, le=8192)
    request_timeout_seconds: float = Field(default=60.0, gt=0)
    mock_llm: bool = False
    prices: dict[str, dict[str, float]] = Field(default_factory=lambda: dict(DEFAULT_PRICES))

    # Guardrails
    rate_limit_per_minute: int = Field(default=20, ge=1)
    max_messages: int = Field(default=30, ge=1)
    max_message_chars: int = Field(default=8000, ge=1)

    @property
    def llm_configured(self) -> bool:
        """True when the app can serve answers (live key or mock mode)."""
        return self.mock_llm or bool(
            self.anthropic_api_key and self.anthropic_api_key.get_secret_value().strip()
        )

    def cost_usd(self, model: str, input_tokens: int, output_tokens: int) -> float:
        price = self.prices.get(model)
        if not price:
            return 0.0
        return (input_tokens * price["input"] + output_tokens * price["output"]) / 1_000_000


@lru_cache
def get_settings() -> Settings:
    return Settings()
