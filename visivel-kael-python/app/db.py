from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator
import psycopg
from psycopg.rows import dict_row
from .config import settings

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS leads (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL DEFAULT '',
    company TEXT NOT NULL DEFAULT '',
    phone VARCHAR(20) NOT NULL UNIQUE,
    niche TEXT NOT NULL DEFAULT '',
    city TEXT NOT NULL DEFAULT '',
    consent_status TEXT NOT NULL DEFAULT 'unknown' CHECK (consent_status IN ('opt_in','opt_out','unknown')),
    consent_source TEXT,
    consent_at TEXT,
    replied_at TIMESTAMPTZ,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS suppressions (
    phone VARCHAR(20) PRIMARY KEY,
    reason TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS campaigns (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'paused' CHECK (status IN ('paused','active','archived')),
    messages JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS message_jobs (
    id BIGSERIAL PRIMARY KEY,
    lead_id BIGINT NOT NULL REFERENCES leads(id),
    campaign_id BIGINT NOT NULL REFERENCES campaigns(id),
    step_no INTEGER NOT NULL DEFAULT 0,
    due_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','processing','sent','dry_run','cancelled','failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT,
    sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (lead_id, campaign_id, step_no)
);
CREATE INDEX IF NOT EXISTS idx_message_jobs_due ON message_jobs(status, due_at);
CREATE TABLE IF NOT EXISTS message_events (
    id BIGSERIAL PRIMARY KEY,
    lead_id BIGINT REFERENCES leads(id),
    campaign_id BIGINT REFERENCES campaigns(id),
    job_id BIGINT REFERENCES message_jobs(id),
    direction TEXT NOT NULL CHECK (direction IN ('inbound','outbound','system')),
    status TEXT NOT NULL,
    message_text TEXT NOT NULL DEFAULT '',
    provider_message_id TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_message_events_created ON message_events(created_at DESC);
"""


@contextmanager
def connection() -> Iterator[psycopg.Connection]:
    conn = psycopg.connect(settings.database_url, row_factory=dict_row)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with connection() as conn:
        conn.execute(SCHEMA_SQL)
