from __future__ import annotations

import hmac
import logging
from fastapi import FastAPI, Header, HTTPException, Request
from .config import settings
from .db import connection
from .normalization import normalize_phone
from .webhook_utils import extract_message, is_opt_out_message

log = logging.getLogger("kael.webhook")
app = FastAPI(title="Visível / KAEL", version="0.1.0")


def _set_opt_out(phone: str, text: str) -> None:
    with connection() as conn:
        lead = conn.execute("SELECT id FROM leads WHERE phone=%s", (phone,)).fetchone()
        conn.execute("INSERT INTO suppressions (phone,reason) VALUES (%s,%s) ON CONFLICT (phone) DO UPDATE SET reason=EXCLUDED.reason, created_at=NOW()",
                     (phone, f"Pedido do contato: {text[:120]}"))
        conn.execute("UPDATE leads SET consent_status='opt_out', active=FALSE, updated_at=NOW() WHERE phone=%s", (phone,))
        if lead:
            conn.execute("UPDATE message_jobs SET status='cancelled', last_error='Opt-out recebido' WHERE lead_id=%s AND status IN ('pending','processing')", (lead["id"],))
            conn.execute("INSERT INTO message_events (lead_id,direction,status,message_text) VALUES (%s,'inbound','opt_out',%s)", (lead["id"], text[:4000]))


def _record_reply(phone: str, text: str, provider_message_id: str | None) -> None:
    with connection() as conn:
        lead = conn.execute("SELECT id FROM leads WHERE phone=%s", (phone,)).fetchone()
        if not lead:
            log.info("Resposta recebida de número não importado; não criando lead automaticamente")
            return
        conn.execute("UPDATE leads SET replied_at=COALESCE(replied_at,NOW()), updated_at=NOW() WHERE id=%s", (lead["id"],))
        conn.execute("UPDATE message_jobs SET status='cancelled', last_error='Resposta recebida; cadência interrompida' WHERE lead_id=%s AND status IN ('pending','processing')", (lead["id"],))
        conn.execute("INSERT INTO message_events (lead_id,direction,status,message_text,provider_message_id) VALUES (%s,'inbound','received',%s,%s)",
                     (lead["id"], text[:4000], provider_message_id))


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "visivel-kael", "send_mode": settings.send_mode}


@app.post("/webhook/evolution")
async def evolution_webhook(request: Request, x_webhook_secret: str | None = Header(default=None)) -> dict:
    expected = settings.webhook_secret
    if not expected or not x_webhook_secret or not hmac.compare_digest(x_webhook_secret, expected):
        raise HTTPException(status_code=401, detail="Webhook não autorizado")
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="JSON inválido") from exc
    event_name = str(payload.get("event", "")).lower()
    if event_name and "messages.upsert" not in event_name:
        return {"ok": True, "ignored": True}
    extracted = extract_message(payload)
    if not extracted:
        return {"ok": True, "ignored": True, "reason": "evento sem texto/mensagem"}
    remote, text, from_me, provider_id = extracted
    if from_me:
        return {"ok": True, "ignored": True, "reason": "mensagem enviada pela própria instância"}
    if "@g.us" in remote or "status@broadcast" in remote:
        return {"ok": True, "ignored": True, "reason": "grupo/status ignorado"}
    phone = normalize_phone(remote.split("@")[0])
    if not phone:
        return {"ok": True, "ignored": True, "reason": "telefone inválido"}
    if is_opt_out_message(text):
        _set_opt_out(phone, text)
        return {"ok": True, "opt_out": True}
    _record_reply(phone, text, provider_id)
    return {"ok": True, "reply_recorded": True}
