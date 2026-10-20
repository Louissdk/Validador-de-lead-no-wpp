from __future__ import annotations

import re


def extract_message(payload: dict) -> tuple[str, str, bool, str | None] | None:
    """Extract the common Evolution API messages.upsert event shape."""
    data = payload.get("data", payload)
    if isinstance(data, list):
        data = data[0] if data else {}
    if not isinstance(data, dict):
        return None
    key = data.get("key") or {}
    remote = key.get("remoteJid") or key.get("remoteJidAlt") or ""
    from_me = bool(key.get("fromMe", False))
    msg = data.get("message") or {}
    text = (
        msg.get("conversation")
        or (msg.get("extendedTextMessage") or {}).get("text")
        or (msg.get("imageMessage") or {}).get("caption")
        or ""
    )
    msg_id = key.get("id")
    if not remote or not text:
        return None
    return remote, str(text).strip(), from_me, msg_id


OPT_OUT_PHRASES = ("SAIR", "PARAR", "PARE", "CANCELAR", "STOP", "REMOVER", "NÃO QUERO", "NAO QUERO")


def is_opt_out_message(text: str) -> bool:
    upper = " ".join(re.sub(r"[.,!?;:]+", " ", str(text or "").upper()).split())
    return any(upper == phrase or upper.startswith(phrase + " ") for phrase in OPT_OUT_PHRASES)
