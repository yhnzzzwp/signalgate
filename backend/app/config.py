from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    sectors_api_key: str
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    gemini_api_key: str | None = None
    signalgate_db_path: str = "./signalgate.db"


@lru_cache
def get_settings() -> Settings:
    return Settings()
