"""Environment-only service configuration."""

from functools import lru_cache
from typing import Annotated

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="CATALOGUE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: SecretStr = SecretStr("sqlite+pysqlite:///./catalogue-dev.db")
    submission_token: SecretStr | None = None
    admin_token: SecretStr | None = None
    monthly_automatic_lookups: int = Field(default=10, ge=0, le=10_000)
    consensus_credit_threshold: float = Field(default=0.90, ge=0.50, le=1.0)
    support_minimum_cents: int = Field(default=1_000, ge=100, le=100_000)
    support_rate_min_cents: int = Field(default=1, ge=1, le=100)
    support_rate_max_cents: int = Field(default=100, ge=1, le=100)
    support_default_rate_cents: int = Field(default=10, ge=1, le=100)
    support_terms_version: str = Field(
        default="2026-08-10", pattern=r"^[0-9]{4}-[0-9]{2}-[0-9]{2}$"
    )
    support_payments_enabled: bool = False
    stripe_secret_key: SecretStr | None = None
    stripe_webhook_secret: SecretStr | None = None
    stripe_success_url: str = Field(
        default="https://ripweaver.com/support/success?session_id={CHECKOUT_SESSION_ID}",
        pattern=r"^https://",
    )
    stripe_cancel_url: str = Field(
        default="https://ripweaver.com/support/cancelled", pattern=r"^https://"
    )
    allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "testserver"]
    )

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def split_allowed_hosts(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value

    @field_validator("support_rate_max_cents")
    @classmethod
    def support_rate_range_must_be_ordered(cls, value: int, info) -> int:
        minimum = info.data.get("support_rate_min_cents", 1)
        if value < minimum:
            raise ValueError("support rate maximum must not be below its minimum")
        return value

    @field_validator("support_default_rate_cents")
    @classmethod
    def default_support_rate_must_be_in_range(cls, value: int, info) -> int:
        minimum = info.data.get("support_rate_min_cents", 1)
        maximum = info.data.get("support_rate_max_cents", 100)
        if not minimum <= value <= maximum:
            raise ValueError("default support rate must be inside the configured range")
        return value

    @property
    def stripe_is_ready(self) -> bool:
        return bool(
            self.support_payments_enabled
            and self.stripe_secret_key
            and self.stripe_secret_key.get_secret_value().strip()
            and self.stripe_webhook_secret
            and self.stripe_webhook_secret.get_secret_value().strip()
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
