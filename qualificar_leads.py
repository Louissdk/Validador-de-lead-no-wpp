"""
VisívelAgora — Qualificador de Leads v4 (FIXED PRODUCTION)
==========================================================
- Corrige abas leads_raw_1k / dinâmicas
- Dedup robusto (sheet + sessão + hash)
- WhatsApp validation com batch + rate limit
- Cursor persistente (não reprocessa leads)
- Escrita otimizada em batch
"""

import json
import re
import time
import signal
import logging
import random
import hashlib
from datetime import datetime
from pathlib import Path

import requests
import gspread
from google.oauth2.service_account import Credentials

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

SPREADSHEET_ID = "1yiZQnUqumPgVRv9DbF3aQzgwbJt6L3RlaucvesXKxSc"

EVOLUTION_URL    = "https://n8n-evolution-api.6laxw2.easypanel.host"
EVOLUTION_APIKEY = "429683C4C977415CAAFCCE10F7D57E11"
EVOLUTION_INST   = "Visivel agora"

GOOGLE_CREDS_FILE = "credentials.json"
ESTADO_FILE       = "estado.json"

LOTE_SIZE = 10

ABAS = [
    f"leads_raw_{i}" for i in range(1, 17)
] + [
    "leads_raw_1k"   # <- FIX CRÍTICO que você pediu
]

BASE_DELAY = (20, 50)
NUM_DELAY  = (0.3, 1.2)

DRY_RUN = False

# ─────────────────────────────────────────────
# LOG
# ─────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# SHUTDOWN
# ─────────────────────────────────────────────

_shutdown = False

def stop(signum, frame):
    global _shutdown
    _shutdown = True
    log.info("Encerrando com segurança...")

signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)

# ─────────────────────────────────────────────
# ESTADO
# ─────────────────────────────────────────────

def load_state():
    if Path(ESTADO_FILE).exists():
        return json.load(open(ESTADO_FILE, "r", encoding="utf-8"))
    return {"aba": 0, "cursor": 0}

def save_state(s):
    json.dump(s, open(ESTADO_FILE, "w", encoding="utf-8"), indent=2)

# ─────────────────────────────────────────────
# SHEETS
# ─────────────────────────────────────────────

def connect():
    creds = Credentials.from_service_account_file(
        GOOGLE_CREDS_FILE,
        scopes=[
            "https://www.googleapis.com/auth/spreadsheets",
            "https://www.googleapis.com/auth/drive"
        ]
    )
    return gspread.authorize(creds).open_by_key(SPREADSHEET_ID)

def get_sheet(sh, name):
    try:
        return sh.worksheet(name).get_all_records()
    except:
        return []

def get_validated_numbers(sh):
    try:
        col = sh.worksheet("leads_validados").col_values(1)
        return {normalize(n) for n in col if normalize(n)}
    except:
        return set()

# ─────────────────────────────────────────────
# NORMALIZAÇÃO (FIX CRÍTICO)
# ─────────────────────────────────────────────

def normalize(n):
    if not n:
        return None

    n = re.sub(r"\D", "", str(n))

    if n.startswith("0055"):
        n = n[4:]
    if n.startswith("55"):
        n = n[2:]

    if len(n) < 10:
        return None

    return "55" + n

def make_hash(lead):
    return hashlib.md5(
        f"{lead['numero']}|{lead['nome']}|{lead['endereco']}".encode()
    ).hexdigest()

# ─────────────────────────────────────────────
# RATE LIMIT
# ─────────────────────────────────────────────

def human_delay():
    time.sleep(random.uniform(*NUM_DELAY))

def batch_delay():
    time.sleep(random.uniform(*BASE_DELAY))

# ─────────────────────────────────────────────
# EVOLUTION API
# ─────────────────────────────────────────────

def check_whatsapp(numbers):
    if DRY_RUN:
        return {n: True for n in numbers}

    url = f"{EVOLUTION_URL}/chat/whatsappNumbers/{requests.utils.quote(EVOLUTION_INST)}"
    headers = {"apikey": EVOLUTION_APIKEY}

    for i in range(3):
        try:
            r = requests.post(url, json={"numbers": numbers}, headers=headers, timeout=30)

            if r.status_code == 429:
                time.sleep(60)
                continue

            if r.ok:
                data = r.json()
                return {
                    re.sub(r"\D", "", d.get("number","")): d.get("exists", False)
                    for d in data if isinstance(d, dict)
                }

        except Exception as e:
            log.error(f"API error: {e}")
            time.sleep(2 ** i)

    return {}

# ─────────────────────────────────────────────
# CORE
# ─────────────────────────────────────────────

def run():
    sh = connect()
    state = load_state()

    aba_i = state["aba"]
    cursor = state["cursor"]

    validated = get_validated_numbers(sh)
    session_seen = set()

    while aba_i < len(ABAS) and not _shutdown:

        sheet_name = ABAS[aba_i]
        log.info(f"Aba: {sheet_name}")

        rows = get_sheet(sh, sheet_name)

        if not rows:
            aba_i += 1
            cursor = 0
            continue

        while cursor < len(rows) and not _shutdown:

            batch = []
            batch_hashes = set()

            while len(batch) < LOTE_SIZE and cursor < len(rows):
                r = rows[cursor]
                cursor += 1

                numero = normalize(r.get("phoneNumber") or r.get("phone"))
                if not numero:
                    continue

                if numero in validated:
                    continue

                lead = {
                    "numero": numero,
                    "nome": r.get("title") or r.get("nome") or "",
                    "endereco": r.get("address") or "",
                    "nicho": r.get("type") or "geral"
                }

                h = make_hash(lead)

                if h in session_seen or h in batch_hashes:
                    continue

                session_seen.add(h)
                batch_hashes.add(h)
                batch.append(lead)

            if not batch:
                continue

            numbers = [b["numero"] for b in batch]

            for n in numbers:
                human_delay()

            result = check_whatsapp(numbers)

            approved = []

            for b in batch:
                if result.get(re.sub(r"\D", "", b["numero"])):
                    approved.append(b)
                    validated.add(b["numero"])

            if approved:
                ws = sh.worksheet("leads_validados")

                ws.append_rows([
                    [a["numero"], a["nome"], a["nicho"], a["endereco"]]
                    for a in approved
                ])

            save_state({"aba": aba_i, "cursor": cursor})

            batch_delay()

        aba_i += 1
        cursor = 0

    log.info("Finalizado com sucesso.")

# ─────────────────────────────────────────────
# START
# ─────────────────────────────────────────────

if __name__ == "__main__":
    run()
