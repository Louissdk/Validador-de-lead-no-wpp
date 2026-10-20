from __future__ import annotations

import re
from typing import Any

PHONE_RE = re.compile(r"\D")
VALID_CONSENT = {"opt_in", "opt_out", "unknown"}


def normalize_phone(value: Any) -> str:
    """Normalize phone numbers to digits and infer Brazilian country code when absent."""
    digits = PHONE_RE.sub("", str(value or ""))
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("55"):
        pass
    elif len(digits) in {10, 11}:
        digits = "55" + digits
    if not (12 <= len(digits) <= 13) or not digits.startswith("55"):
        return ""
    return digits


def normalize_consent(status: Any, source: Any, consent_at: Any) -> tuple[str, str | None, str | None]:
    state = str(status or "unknown").strip().lower().replace(" ", "_")
    if state not in VALID_CONSENT:
        state = "unknown"
    source_text = str(source or "").strip() or None
    date_text = str(consent_at or "").strip() or None
    if state == "opt_in" and not (source_text and date_text):
        state = "unknown"
    return state, source_text, date_text


def render_template(template: str, lead: dict[str, Any]) -> str:
    """Render only known fields; unknown placeholders fail loudly."""
    allowed = {key: str(lead.get(key) or "") for key in ("nome", "empresa", "nicho", "cidade")}
    try:
        return template.format_map(allowed).strip()
    except KeyError as exc:
        raise ValueError(f"Campo de template não permitido: {exc.args[0]}") from exc
