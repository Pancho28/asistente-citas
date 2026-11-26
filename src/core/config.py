from functools import lru_cache
from zoneinfo import ZoneInfo
from datetime import datetime, timezone
from typing import Any
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    ENVIRONMENT: str = "development"
    DEFAULT_TIMEZONE: str = "America/Caracas"
    PORT: int = 8005

    DATABASE_URL: str = (
        "postgresql+asyncpg://reservas_user:reservas_super_secret_password_2026"
        "@127.0.0.1:5433/citas_medicas"
    )
    DB_POOL_SIZE: int = Field(default=5, ge=1)
    DB_MAX_OVERFLOW: int = Field(default=5, ge=0)
    DB_POOL_TIMEOUT: int = Field(default=15, ge=1)
    REDIS_URL: str = "redis://:redis_secure_password_2026@127.0.0.1:6380/1"

    INTERNAL_GATEWAY_SECRET: str = "clave_secreta_compartida_con_el_gateway"

    # Validación Criptográfica Modular de Webhooks
    TELEGRAM_WEBHOOK_SECRET: str = "tu_secret_token_de_telegram_webhook"
    WHATSAPP_APP_SECRET: str = "tu_app_secret_de_meta_para_hmac_sha256"
    WHATSAPP_VERIFY_TOKEN: str = "tu_verify_token_para_hub_challenge"

    GEMINI_API_KEY: str = ""
    IA_MODELS: list[str] = [
        "gemini-3.5-flash",
        "gemini-3.7-flash",
        "gemini-4-hydrogen",
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

    @field_validator("IA_MODELS", mode="before")
    @classmethod
    def parse_ia_models(cls, v: Any) -> list[str]:
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
        return list(dict.fromkeys(self.IA_MODELS))

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore"
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


COMMON_TZ_ALIASES: dict[str, str] = {
    "caracas": "America/Caracas",
    "venezuela": "America/Caracas",
    "bogota": "America/Bogota",
    "colombia": "America/Bogota",
    "lima": "America/Lima",
    "quito": "America/Guayaquil",
    "santiago": "America/Santiago",
    "buenos_aires": "America/Argentina/Buenos_Aires",
    "argentina": "America/Argentina/Buenos_Aires",
    "mexico": "America/Mexico_City",
    "cdmx": "America/Mexico_City",
    "madrid": "Europe/Madrid",
    "espana": "Europe/Madrid",
    "miami": "America/New_York",
}


def normalize_timezone_name(tz_name: str | None) -> str:
    """Normaliza y valida un nombre de zona horaria IANA o alias común."""
    settings = get_settings()
    if not tz_name or not tz_name.strip():
        return settings.DEFAULT_TIMEZONE
    raw = tz_name.strip()
    key = raw.lower().replace(" ", "_")
    candidate = COMMON_TZ_ALIASES.get(key, raw)
    ZoneInfo(candidate)
    return candidate


def resolve_zoneinfo(tz_name: str | None = None) -> ZoneInfo:
    """Resuelve ZoneInfo seguro con fallback a DEFAULT_TIMEZONE."""
    settings = get_settings()
    try:
        return ZoneInfo(normalize_timezone_name(tz_name))
    except Exception:
        return ZoneInfo(settings.DEFAULT_TIMEZONE)


def now_tz(tz_name: str | None = None) -> datetime:
    """Devuelve la fecha y hora actual en la zona horaria indicada (o DEFAULT_TIMEZONE)."""
    return datetime.now(resolve_zoneinfo(tz_name))


def now_wall_utc(tz_name: str | None = None) -> datetime:
    """
    Proyecta la fecha/hora local de la zona horaria indicada con tzinfo=timezone.utc
    para comparar de forma consistente contra columnas nominales TIMESTAMPTZ.
    """
    return now_tz(tz_name).replace(tzinfo=timezone.utc)


def format_wall_time(dt: datetime, fmt: str = "%H:%M") -> str:
    """Formatea la hora nominal guardada en UTC sin desplazamiento por sesión de BD."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).strftime(fmt)
    return dt.strftime(fmt)
