from __future__ import annotations

import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "sim", "on"}


@dataclass(frozen=True)
class Settings:
    database_url: str = os.getenv("DATABASE_URL", "postgresql://kael:change_me_local_password@localhost:5433/kael")
    redis_url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    evolution_base_url: str = os.getenv("EVOLUTION_BASE_URL", "").rstrip("/")
    evolution_api_key: str = os.getenv("EVOLUTION_API_KEY", "")
    evolution_instance: str = os.getenv("EVOLUTION_INSTANCE", "")
    send_mode: str = os.getenv("SEND_MODE", "dry_run").strip().lower()
    allow_marketing_sends: bool = _bool("ALLOW_MARKETING_SENDS", False)
    live_send_confirmation: str = os.getenv("LIVE_SEND_CONFIRMATION", "")
    webhook_secret: str = os.getenv("WEBHOOK_SECRET", "")
    timezone: str = os.getenv("TIMEZONE", "America/Sao_Paulo")
    business_start_hour: int = int(os.getenv("BUSINESS_START_HOUR", "9"))
    business_end_hour: int = int(os.getenv("BUSINESS_END_HOUR", "18"))
    max_messages_per_day: int = int(os.getenv("MAX_MESSAGES_PER_DAY", "20"))
    min_seconds_between_messages: int = int(os.getenv("MIN_SECONDS_BETWEEN_MESSAGES", "45"))
    worker_poll_seconds: int = int(os.getenv("WORKER_POLL_SECONDS", "5"))
    request_timeout_seconds: int = int(os.getenv("REQUEST_TIMEOUT_SECONDS", "20"))

    @property
    def live_send_enabled(self) -> bool:
        return (
            self.send_mode == "live"
            and self.allow_marketing_sends
            and self.live_send_confirmation == "I_UNDERSTAND_AND_HAVE_PERMISSION"
            and bool(self.evolution_base_url and self.evolution_api_key and self.evolution_instance)
        )


settings = Settings()
