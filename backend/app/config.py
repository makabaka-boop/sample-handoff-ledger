from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://handoff:handoff@db:5432/handoff"
    handoff_signing_key: str = Field(min_length=32)
    cors_origins: str = "http://localhost:4173,http://localhost:5173"
    handoff_default_ttl_minutes: int = Field(default=10, ge=1, le=1440)

    @field_validator("handoff_signing_key")
    @classmethod
    def reject_example_secret(cls, value: str) -> str:
        if value in {"replace-me", "change-me", "replace-with-at-least-32-random-characters"}:
            raise ValueError("HANDOFF_SIGNING_KEY must be replaced with a private random value")
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
