from functools import lru_cache
from typing import Literal

from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    telegram_bot_token: str
    telegram_owner_id: int
    telegram_inbox_chat_id: int
    telegram_sumify_chat_id: int
    telegram_webhook_secret: str = Field(min_length=16)
    public_base_url: HttpUrl
    ai_api_key: str
    ai_base_url: HttpUrl = HttpUrl("https://openrouter.ai/api/v1")
    ai_model: str = "openai/gpt-5.4-mini"
    database_url: str
    default_filter_mode: Literal["all", "soft", "medium", "strict"] = "medium"
    default_filter_prompt: str = ""
    log_level: str = "INFO"

    @property
    def telegram_webhook_url(self) -> str:
        return f"{str(self.public_base_url).rstrip('/')}/webhooks/telegram"

    @property
    def youtube_callback_url(self) -> str:
        return f"{str(self.public_base_url).rstrip('/')}/webhooks/youtube"


@lru_cache
def get_settings() -> Settings:
    return Settings()
