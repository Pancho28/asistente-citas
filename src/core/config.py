from functools import lru_cache
from zoneinfo import ZoneInfo
from datetime import datetime
from typing import Any
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ENVIRONMENT: str = "development"
    DEFAULT_TIMEZONE: str = "America/Caracas"
    PORT: int = 8005

    DATABASE_URL: str = "postgresql+asyncpg://postgres:password@127.0.0.1:5433/citas_medicas"
    REDIS_URL: str = "redis://127.0.0.1:6380/0"

    INTERNAL_GATEWAY_SECRET: str = "clave_secreta_compartida_con_el_gateway"

    # Validación Criptográfica Modular de Webhooks
    TELEGRAM_WEBHOOK_SECRET: str = "tu_secret_token_de_telegram_webhook"
    WHATSAPP_APP_SECRET: str = "tu_app_secret_de_meta_para_hmac_sha256"
    WHATSAPP_VERIFY_TOKEN: str = "tu_verify_token_para_hub_challenge"

    GEMINI_API_KEY: str = ""
    GEMINI_MODEL: str = "gemini-2.5-flash"
    GEMINI_FALLBACK_MODELS: list[str] = [
        "gemini-3.5-flash",
        "gemini-3.7-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash-lite",
    ]
    GEMINI_HEDGED_TIMEOUT_SECONDS: float = 4.0

    TELEGRAM_BOT_TOKEN: str = ""
    TELEGRAM_BOT_USERNAME: str = "mi_bot"
    WHATSAPP_ACCESS_TOKEN: str | None = None
    WHATSAPP_PHONE_NUMBER_ID: str | None = None

    # Variables de seed inicial para el Administrador
    INITIAL_ADMIN_NOMBRE: str = "Administrador"
    INITIAL_ADMIN_TELEGRAM_CHAT_ID: str | None = None
    INITIAL_ADMIN_WHATSAPP_PHONE: str | None = None

    @field_validator("DATABASE_URL", mode="before")
    @classmethod
    def normalize_database_url(cls, v: str) -> str:
        """
        Normaliza las URLs nativas entregadas por Railway (postgres:// o postgresql://)
        al driver asíncrono requerido por SQLAlchemy 2.0 (postgresql+asyncpg://).
        """
        if isinstance(v, str):
            if v.startswith("postgres://"):
                return v.replace("postgres://", "postgresql+asyncpg://", 1)
            if v.startswith("postgresql://"):
                return v.replace("postgresql://", "postgresql+asyncpg://", 1)
        return v

    @field_validator("GEMINI_FALLBACK_MODELS", mode="before")
    @classmethod
    def parse_gemini_fallback_models(cls, v: Any) -> list[str]:
        if isinstance(v, str):
            v = v.strip()
            if v.startswith("[") and v.endswith("]"):
                try:
                    import json
                    parsed = json.loads(v)
                    if isinstance(parsed, list):
                        return [str(x).strip() for x in parsed if str(x).strip()]
                except Exception:
                    pass
            return [x.strip() for x in v.split(",") if x.strip()]
        if isinstance(v, (list, tuple)):
            return [str(x).strip() for x in v if str(x).strip()]
        return v

    @property
    def gemini_models_list(self) -> list[str]:
        models = [self.GEMINI_MODEL]
        for m in self.GEMINI_FALLBACK_MODELS:
            if m not in models:
                models.append(m)
        return models

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def now_tz() -> datetime:
    """Devuelve la fecha y hora actual en la zona horaria configurada."""
    settings = get_settings()
    return datetime.now(ZoneInfo(settings.DEFAULT_TIMEZONE))
