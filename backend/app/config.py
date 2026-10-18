from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "INTERVUE API"
    environment: str = "development"
    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_server_key: str = ""
    database_url: str = ""
    frontend_origins: str = "http://localhost:5500,http://127.0.0.1:5500"
    max_cv_bytes: int = 5 * 1024 * 1024
    cv_bucket: str = "candidate-cvs"
    gemini_api_key: str = ""
    gemini_live_model: str = "gemini-3.8-live"
    gemini_evaluation_model: str = "gemini-3.8-flash"
    gemini_evaluation_budget_seconds: float = 120
    gemini_evaluation_attempt_timeout_seconds: float = 50
    gemini_evaluation_max_attempts: int = 2
    groq_api_key: str = ""
    groq_evaluation_model: str = "openai/gpt-oss-20b"
    groq_evaluation_budget_seconds: float = 45
    groq_evaluation_attempt_timeout_seconds: float = 20
    groq_evaluation_max_attempts: int = 2
    gemini_evaluation_fallback_enabled: bool = False
    voice_ticket_ttl_seconds: int = 60
    voice_max_session_minutes: int = 30
    voice_max_chunk_bytes: int = 32768
    voice_max_active_per_user: int = 1

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def cors_origins(self) -> list[str]:
        return [item.strip() for item in self.frontend_origins.split(",") if item.strip()]

    @property
    def configured(self) -> bool:
        return bool(self.supabase_url and self.supabase_anon_key)

    @property
    def gemini_configured(self) -> bool:
        return bool(self.gemini_api_key)

    @property
    def groq_configured(self) -> bool:
        return bool(self.groq_api_key)


@lru_cache
def get_settings() -> Settings:
    return Settings()


