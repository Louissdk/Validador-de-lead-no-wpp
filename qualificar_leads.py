"""
VisívelAgora — Qualificador de Leads v4 (PRODUCTION READY)
==========================================================
- Processa múltiplas abas do Google Sheets
- Dedup robusto (telefone + hash)
- Validação WhatsApp via Evolution API com rate limit
- Gravação otimizada em batch
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
# CONFIGURAÇÕES
# ─────────────────────────────────────────────

SPREADSHEET_ID = "1yiZQnUqumPgVRv9DbF3aQzgwbJt6L3RlaucvesXKxSc"

EVOLUTION_URL    = "https://n8n-evolution-api.6laxw2.easypanel.host"
EVOLUTION_APIKEY = "429683C4C977415CAAFCCE10F7D57E11"
EVOLUTION_INST   = "Visivel agora"

GOOGLE_CREDS_FILE = "credentials.json"
ESTADO_FILE       = "estado.json"
RELATORIO_FILE    = "relatorio_final.json"

ABAS = [f"leads_raw_{i}" for i in range(1, 16)]

LOTE_SIZE = 10

# anti-block inteligente
BASE_DELAY_LOTE = (25, 60)
DELAY_ENTRE_NUMEROS = (0.4, 1.5)

HORA_INICIO = 8
HORA_FIM = 20

DRY_RUN = False

# ─────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# SHUTDOWN CONTROL
# ─────────────────────────────────────────────

_shutdown = False

def handle_signal(signum, frame):
    global _shutdown
    _shutdown = True
    log.info("Encerramento solicitado... finalizando ciclo atual.")

signal.signal(signal.SIGINT, handle_signal)
signal.signal(signal.SIGTERM, handle_signal)

# ─────────────────────────────────────────────
# ESTADO
# ─────────────────────────────────────────────

def carregar_estado():
    if Path(ESTADO_FILE).exists():
        return json.load(open(ESTADO_FILE, "r", encoding="utf-8"))
    return {"aba_index": 0, "cursor": 0, "total": 0}

def salvar_estado(data):
    json.dump(data, open(ESTADO_FILE, "w", encoding="utf-8"), indent=2)

# ─────────────────────────────────────────────
# GOOGLE SHEETS
# ─────────────────────────────────────────────

def conectar():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_file(GOOGLE_CREDS_FILE, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID)

def ler_aba(planilha, nome):
    try:
        return planilha.worksheet(nome).get_all_records()
    except:
        return []

def ler_validados(planilha):
    try:
        col = planilha.worksheet("leads_validados").col_values(1)
        return {
            limpar_numero(v)
            for v in col
            if v and limpar_numero(v)
        }
    except:
        return set()

# ─────────────────────────────────────────────
# NORMALIZAÇÃO
# ─────────────────────────────────────────────

def limpar_numero(v):
    if not v:
        return None
    n = re.sub(r"\D", "", str(v))

    if n.startswith("0055"):
        n = n[4:]
    if n.startswith("55"):
        n = n[2:]

    return "55" + n if len(n) >= 10 else None

def gerar_hash(lead):
    base = f"{lead['numero']}|{lead['nome']}|{lead['endereco']}"
    return hashlib.md5(base.encode()).hexdigest()

# ─────────────────────────────────────────────
# RATE LIMIT (anti-block real)
# ─────────────────────────────────────────────

def delay_humano():
    time.sleep(random.uniform(*DELAY_ENTRE_NUMEROS))

def delay_lote():
    time.sleep(random.uniform(*BASE_DELAY_LOTE))

# ─────────────────────────────────────────────
# EVOLUTION API
# ─────────────────────────────────────────────

def verificar_whatsapp(numeros):
    if DRY_RUN:
        return {n: True for n in numeros}

    url = f"{EVOLUTION_URL}/chat/whatsappNumbers/{requests.utils.quote(EVOLUTION_INST)}"
    headers = {"apikey": EVOLUTION_APIKEY, "Content-Type": "application/json"}

    payload = {"numbers": numeros}

    for i in range(3):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=30)

            if r.status_code == 429:
                log.warning("Rate limit detectado, aguardando 60s...")
                time.sleep(60)
                continue

            if r.ok:
                data = r.json()
                return {
                    re.sub(r"\D", "", item.get("number","")): item.get("exists", False)
                    for item in data
                    if isinstance(item, dict)
                }

        except Exception as e:
            log.error(f"Erro API: {e}")
            time.sleep(2 ** i)

    return {}

# ─────────────────────────────────────────────
# PROCESSAMENTO
# ─────────────────────────────────────────────

def processar():
    global _shutdown

    planilha = conectar()
    estado = carregar_estado()

    aba_index = estado["aba_index"]
    cursor = estado["cursor"]

    validados = ler_validados(planilha)
    vistos = set()

    while aba_index < len(ABAS) and not _shutdown:
        aba_nome = ABAS[aba_index]
        log.info(f"Processando {aba_nome}")

        registros = ler_aba(planilha, aba_nome)

        if not registros:
            aba_index += 1
            cursor = 0
            continue

        while cursor < len(registros) and not _shutdown:

            lote = []
            hashes = set()

            while len(lote) < LOTE_SIZE and cursor < len(registros):
                r = registros[cursor]
                cursor += 1

                numero = limpar_numero(r.get("phoneNumber"))
                if not numero:
                    continue

                if numero in validados:
                    continue

                lead = {
                    "numero": numero,
                    "nome": r.get("title", ""),
                    "endereco": r.get("address", ""),
                    "nicho": r.get("type", "geral"),
                }

                h = gerar_hash(lead)
                if h in hashes or h in vistos:
                    continue

                hashes.add(h)
                vistos.add(h)
                lote.append(lead)

            if not lote:
                continue

            numeros = [l["numero"] for l in lote]

            for n in numeros:
                delay_humano()

            resultado = verificar_whatsapp(numeros)

            aprovados = []
            for l in lote:
                if resultado.get(re.sub(r"\D", "", l["numero"])):
                    aprovados.append(l)
                    validados.add(l["numero"])

            if aprovados:
                sheet = planilha.worksheet("leads_validados")
                sheet.append_rows([[l["numero"], l["nome"], l["nicho"], l["endereco"]] for l in aprovados])

            salvar_estado({
                "aba_index": aba_index,
                "cursor": cursor,
                "total": len(validados)
            })

            delay_lote()

        aba_index += 1
        cursor = 0

    log.info("Finalizado.")

# ─────────────────────────────────────────────
# START
# ─────────────────────────────────────────────

if __name__ == "__main__":
    processar()
