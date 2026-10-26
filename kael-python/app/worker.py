from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import redis
from .config import settings
from .db import connection
from .evolution import EvolutionClient, EvolutionError
from .normalization import render_template

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("kael.worker")


def is_business_time(now: datetime | None = None) -> bool:
    local = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo(settings.timezone))
    return local.weekday() < 5 and settings.business_start_hour <= local.hour < settings.business_end_hour


def _rate_lock() -> tuple[bool, int]:
    try:
        client = redis.Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=2)
        locked = client.set("kael:message-send-lock", "1", nx=True, ex=max(1, settings.min_seconds_between_messages))
        if locked:
            return True, 0
        ttl = client.ttl("kael:message-send-lock")
        return False, max(1, ttl if ttl > 0 else settings.min_seconds_between_messages)
    except redis.RedisError as exc:
        log.error("Redis indisponível; envio bloqueado por segurança: %s", exc)
        return False, max(5, settings.worker_poll_seconds)


def _claim_job():
    with connection() as conn:
        row = conn.execute("""
            SELECT j.*, l.name AS lead_name, l.company AS lead_company, l.phone,
                   l.niche AS lead_niche, l.city AS lead_city, l.consent_status,
                   l.consent_source, l.consent_at, l.replied_at, l.active AS lead_active,
                   c.name AS campaign_name, c.status AS campaign_status, c.messages
            FROM message_jobs j
            JOIN leads l ON l.id = j.lead_id
            JOIN campaigns c ON c.id = j.campaign_id
            WHERE j.status = 'pending' AND j.due_at <= NOW() AND c.status = 'active'
            ORDER BY j.due_at, j.id
            FOR UPDATE OF j SKIP LOCKED LIMIT 1
        """).fetchone()
        if row is None:
            return None
        conn.execute("UPDATE message_jobs SET status='processing', attempts=attempts+1 WHERE id=%s", (row["id"],))
        return row


def _record_skip(job_id: int, lead_id: int, message: str) -> None:
    with connection() as conn:
        conn.execute("UPDATE message_jobs SET status='cancelled', last_error=%s WHERE id=%s", (message[:500], job_id))
        conn.execute("INSERT INTO message_events (lead_id, job_id, direction, status, message_text) VALUES (%s,%s,'system','blocked',%s)", (lead_id, job_id, message[:500]))


def _record_failure(job_id: int, lead_id: int, error: str, attempts: int) -> None:
    status = "pending" if attempts < 3 else "failed"
    delay_minutes = min(60, 2 ** attempts * 2)
    with connection() as conn:
        conn.execute("UPDATE message_jobs SET status=%s, due_at=NOW() + (%s * INTERVAL '1 minute'), last_error=%s WHERE id=%s",
                     (status, delay_minutes, error[:500], job_id))
        conn.execute("INSERT INTO message_events (lead_id, job_id, direction, status, message_text) VALUES (%s,%s,'system','error',%s)",
                     (lead_id, job_id, error[:500]))


def _daily_send_count() -> int:
    with connection() as conn:
        return conn.execute("SELECT COUNT(*) AS n FROM message_jobs WHERE status='sent' AND sent_at >= date_trunc('day', NOW() AT TIME ZONE %s) AT TIME ZONE %s",
                            (settings.timezone, settings.timezone)).fetchone()["n"]


def _finish_job(job: dict, final_status: str, text: str, provider_id: str | None = None) -> None:
    with connection() as conn:
        conn.execute("UPDATE message_jobs SET status=%s, sent_at=CASE WHEN %s='sent' THEN NOW() ELSE sent_at END, last_error=NULL WHERE id=%s",
                     (final_status, final_status, job["id"]))
        conn.execute("INSERT INTO message_events (lead_id,campaign_id,job_id,direction,status,message_text,provider_message_id) VALUES (%s,%s,%s,'outbound',%s,%s,%s)",
                     (job["lead_id"], job["campaign_id"], job["id"], final_status, text[:4000], provider_id))
        if final_status == "sent" and job["step_no"] == 0:
            for step_no, follow in enumerate(job["messages"].get("follow_ups", []), start=1):
                days = max(1, int(follow.get("days_after", step_no)))
                conn.execute("""
                    INSERT INTO message_jobs (lead_id,campaign_id,step_no,due_at)
                    VALUES (%s,%s,%s,NOW() + (%s * INTERVAL '1 day'))
                    ON CONFLICT (lead_id,campaign_id,step_no) DO NOTHING
                """, (job["lead_id"], job["campaign_id"], step_no, days))


def process_one() -> bool:
    job = _claim_job()
    if not job:
        return False
    job_id, lead_id = job["id"], job["lead_id"]
    if job["consent_status"] != "opt_in" or not job["consent_source"] or not job["consent_at"]:
        _record_skip(job_id, lead_id, "Sem consentimento opt-in completo/documentado")
        return True
    if not job["lead_active"] or job["replied_at"] is not None:
        _record_skip(job_id, lead_id, "Lead inativo ou já respondeu; cadência encerrada")
        return True
    with connection() as conn:
        suppressed = conn.execute("SELECT 1 FROM suppressions WHERE phone=%s", (job["phone"],)).fetchone()
    if suppressed:
        _record_skip(job_id, lead_id, "Número está na lista de supressão")
        return True
    if not is_business_time():
        with connection() as conn:
            conn.execute("UPDATE message_jobs SET status='pending' WHERE id=%s", (job_id,))
        return False
    if _daily_send_count() >= settings.max_messages_per_day:
        with connection() as conn:
            conn.execute("UPDATE message_jobs SET status='pending' WHERE id=%s", (job_id,))
        log.warning("Limite diário alcançado (%s); fila pausada", settings.max_messages_per_day)
        return False
    follow_ups = job["messages"].get("follow_ups", [])
    if job["step_no"] == 0:
        template = job["messages"].get("initial", "")
    else:
        idx = job["step_no"] - 1
        template = follow_ups[idx]["text"] if idx < len(follow_ups) else ""
    if not template:
        _record_skip(job_id, lead_id, "Template ausente para esta etapa")
        return True
    lead = {"nome": job["lead_name"], "empresa": job["lead_company"], "nicho": job["lead_niche"], "cidade": job["lead_city"]}
    try:
        message = render_template(template, lead)
    except ValueError as exc:
        _record_skip(job_id, lead_id, str(exc))
        return True
    if not settings.live_send_enabled:
        log.info("DRY RUN — lead=%s campanha=%s etapa=%s mensagem=%r", job["phone"], job["campaign_name"], job["step_no"], message)
        _finish_job(job, "dry_run", message)
        return True
    allowed, wait_seconds = _rate_lock()
    if not allowed:
        with connection() as conn:
            conn.execute("UPDATE message_jobs SET status='pending' WHERE id=%s", (job_id,))
        time.sleep(wait_seconds)
        return False
    try:
        provider_id = EvolutionClient().send_text(job["phone"], message)
    except EvolutionError as exc:
        log.error("Envio falhou para lead_id=%s: %s", lead_id, exc)
        _record_failure(job_id, lead_id, str(exc), job["attempts"])
        return True
    log.info("Mensagem enviada: lead_id=%s campanha=%s etapa=%s", lead_id, job["campaign_id"], job["step_no"])
    _finish_job(job, "sent", message, provider_id)
    return True


def run_worker(once: bool = False) -> None:
    if settings.send_mode not in {"dry_run", "live"}:
        raise RuntimeError("SEND_MODE precisa ser dry_run ou live")
    if settings.send_mode == "live" and not settings.live_send_enabled:
        raise RuntimeError("Envio real bloqueado: configure explicitamente SEND_MODE, ALLOW_MARKETING_SENDS, LIVE_SEND_CONFIRMATION e Evolution API")
    log.info("Worker KAEL iniciado; modo=%s", settings.send_mode)
    while True:
        try:
            worked = process_one()
        except Exception:
            log.exception("Erro não tratado ao processar fila; processo continuará no próximo ciclo")
            worked = False
        if once:
            break
        if not worked:
            time.sleep(max(1, settings.worker_poll_seconds))
        elif settings.send_mode == "dry_run":
            time.sleep(0.2)
