from __future__ import annotations

import requests
from .config import settings


class EvolutionError(RuntimeError):
    pass


class EvolutionClient:
    def __init__(self) -> None:
        self.base_url = settings.evolution_base_url
        self.api_key = settings.evolution_api_key
        self.instance = settings.evolution_instance

    def send_text(self, phone: str, text: str) -> str | None:
        if not (self.base_url and self.api_key and self.instance):
            raise EvolutionError("Evolution API não configurada; mensagem não enviada")
        url = f"{self.base_url}/message/sendText/{self.instance}"
        try:
            response = requests.post(
                url,
                headers={"apikey": self.api_key, "Content-Type": "application/json"},
                json={"number": phone, "text": text},
                timeout=settings.request_timeout_seconds,
            )
        except requests.RequestException as exc:
            raise EvolutionError(f"Falha de conexão com Evolution API: {exc}") from exc
        if response.status_code >= 300:
            safe_body = response.text[:300].replace(settings.evolution_api_key, "[REDACTED]")
            raise EvolutionError(f"Evolution API retornou HTTP {response.status_code}: {safe_body}")
        try:
            data = response.json()
        except ValueError:
            return None
        key = data.get("key", {}) if isinstance(data, dict) else {}
        return key.get("id") or (data.get("message", {}).get("id") if isinstance(data, dict) and isinstance(data.get("message"), dict) else None)
