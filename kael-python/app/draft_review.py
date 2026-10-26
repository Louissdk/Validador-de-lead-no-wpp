from __future__ import annotations

from string import Formatter
from urllib.parse import urlencode

from .normalization import normalize_phone, render_template

ALLOWED_FIELDS = {"nome", "empresa", "nicho", "cidade"}
MAX_BATCH_LIMIT = 10000


def validate_template(template: str) -> set[str]:
    """Validate that a template uses only supported placeholders."""
    if not template or not template.strip():
        raise ValueError("O modelo da mensagem não pode ficar vazio.")
    if len(template) > 4000:
        raise ValueError("O modelo de mensagem deve ter no máximo 4.000 caracteres.")
    fields: set[str] = set()
    try:
        for _, field_name, format_spec, conversion in Formatter().parse(template):
            if field_name is None:
                continue
            if field_name not in ALLOWED_FIELDS or format_spec or conversion:
                raise ValueError(f"Campo de template não permitido: {field_name}")
            fields.add(field_name)
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Modelo de mensagem inválido.") from exc
    return fields


def missing_template_fields(template: str, lead: dict) -> list[str]:
    """Avoid generating awkward messages with empty personalized values."""
    required = validate_template(template)
    mapping = {
        "nome": str(lead.get("nome") or "").strip(),
        "empresa": str(lead.get("empresa") or "").strip(),
        "nicho": str(lead.get("nicho") or "").strip(),
        "cidade": str(lead.get("cidade") or "").strip(),
    }
    return sorted(key for key in required if not mapping[key])


def render_review_message(template: str, lead: dict) -> str:
    missing = missing_template_fields(template, lead)
    if missing:
        raise ValueError("Faltam dados para personalizar a mensagem: " + ", ".join(missing))
    message = render_template(template, lead)
    if not message:
        raise ValueError("A mensagem gerada ficou vazia.")
    if len(message) > 4000:
        raise ValueError("A mensagem gerada ultrapassa 4.000 caracteres.")
    return message


def build_whatsapp_url(raw_phone: str, message: str) -> str:
    """Return a click-to-chat URL. Opening it does not send the message."""
    phone = normalize_phone(raw_phone)
    if not phone:
        raise ValueError("Telefone inválido.")
    if not message or not message.strip():
        raise ValueError("A mensagem não pode ficar vazia.")
    return "https://web.whatsapp.com/send?" + urlencode({"phone": phone, "text": message})
