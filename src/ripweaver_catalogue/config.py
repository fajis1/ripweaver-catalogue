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
    allowed_hosts: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["localhost", "127.0.0.1", "testserver"]
    )

    @field_validator("allowed_hosts", mode="before")
    @classmethod
    def split_allowed_hosts(cls, value: object) -> object:
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
