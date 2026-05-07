"""
VisívelAgora — Qualificador de Leads v6
=======================================

CORREÇÕES E MELHORIAS v6
------------------------
[FIX CRÍTICO]
- API da Evolution agora diferencia:
    True  = possui WhatsApp
    False = não possui WhatsApp
    None  = erro na API

- Leads NÃO são marcados como processados quando a API falha.
- Escrita atômica para evitar corrupção de JSON.
- Compatível com Python 3.11+
- Controle melhor de memória.
- Persistência segura.
- Logs expandidos.
- Melhor proteção contra timeout.

ABAS NECESSÁRIAS NO GOOGLE SHEETS
---------------------------------
1) leads_validados
COLUNAS:
numero | nome | nicho | nicho_group | endereco | rating | tem_site |
origem_aba | score | priority_level | whatsapp_confirmado | data_verificacao

2) leads_sem_wpp
COLUNAS:
numero | nome | nicho | origem_aba | data_verificacao
"""

import json
import re
import time
import signal
import logging
import random
from datetime import datetime
from pathlib import Path

import requests
import gspread
from google.oauth2.service_account import Credentials

# ============================================================
# CONFIG
# ============================================================

SPREADSHEET_ID = "SEU_SPREADSHEET_ID"
EVOLUTION_URL = "https://SEU-ENDPOINT"
EVOLUTION_APIKEY = "SUA_API_KEY"
EVOLUTION_INST = "Visivel agora"

GOOGLE_CREDS_FILE = "credentials.json"
ESTADO_FILE = "estado.json"
PROCESSADOS_FILE = "processados.json"
RELATORIO_FILE = "relatorio_final.json"

LOTE_SIZE = 30
DELAY_ENTRE_LOTES = 5
DELAY_JITTER = 5

DRY_RUN = False
FORCAR_RESET = False

ABAS = [
    "leads_raw",
    "leads_raw_1k",
    "leads_raw_2k",
    "leads_raw_3k",
    "leads_raw_4k",
    "leads_raw_5k",
    "leads_raw_6k",
    "leads_raw_7k",
    "leads_raw_8k",
    "leads_raw_9k",
    "leads_raw_10k",
    "leads_raw_11k",
    "leads_raw_12k",
    "leads_raw_13k",
    "leads_raw_14k",
    "leads_raw_15k",
]

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("qualificar_leads.log", encoding="utf-8"),
    ]
)

log = logging.getLogger(__name__)

# ============================================================
# GRACEFUL SHUTDOWN
# ============================================================

_shutdown = False


def _handle_signal(signum, frame):
    global _shutdown
    log.warning("Encerramento solicitado. Finalizando lote atual...")
    _shutdown = True


signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)

# ============================================================
# JSON SAFE WRITE
# ============================================================


def atomic_write_json(path: str, data: dict):
    temp_path = f"{path}.tmp"

    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

    Path(temp_path).replace(path)

# ============================================================
# ESTADO
# ============================================================


def carregar_estado() -> dict:
    if Path(ESTADO_FILE).exists():
        with open(ESTADO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)

    return {
        "aba_index": 0,
        "cursor": 0,
        "total_gravados": 0,
    }



def salvar_estado(estado: dict):
    atomic_write_json(ESTADO_FILE, estado)

# ============================================================
# PROCESSADOS
# ============================================================


def carregar_processados() -> set:
    if not Path(PROCESSADOS_FILE).exists():
        return set()

    with open(PROCESSADOS_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)

    return set(data.get("numeros", []))



def salvar_processados(numeros: set):
    atomic_write_json(PROCESSADOS_FILE, {
        "numeros": list(numeros),
        "atualizado_em": datetime.now().isoformat()
    })

# ============================================================
# GOOGLE SHEETS
# ============================================================


def conectar_sheets():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]

    creds = Credentials.from_service_account_file(
        GOOGLE_CREDS_FILE,
        scopes=scopes
    )

    client = gspread.authorize(creds)

    return client.open_by_key(SPREADSHEET_ID)



def ler_aba(planilha, nome_aba: str):
    try:
        aba = planilha.worksheet(nome_aba)
        registros = aba.get_all_records(empty2zero=False, head=1)

        log.info(f"{nome_aba}: {len(registros)} linhas lidas")

        return registros

    except Exception as e:
        log.error(f"Erro lendo aba {nome_aba}: {e}")
        return []



def ler_numeros_validados(planilha):
    try:
        aba = planilha.worksheet("leads_validados")

        valores = aba.col_values(1)

        numeros = set(
            re.sub(r"\D", "", v)
            for v in valores[1:]
            if v
        )

        log.info(f"{len(numeros)} números já validados")

        return numeros

    except Exception as e:
        log.warning(f"Erro lendo leads_validados: {e}")
        return set()

# ============================================================
# NORMALIZAÇÃO
# ============================================================


def normalizar_numero(raw):
    numero = re.sub(r"\D", "", str(raw or ""))

    if not numero:
        return None

    if numero.startswith("0055"):
        numero = numero[4:]

    elif numero.startswith("55"):
        numero = numero[2:]

    if len(numero) < 10 or len(numero) > 11:
        return None

    return f"55{numero}"

# ============================================================
# NICHO GROUP
# ============================================================


def nicho_group(nicho: str):
    n = (nicho or "").lower()

    if any(k in n for k in [
        "pizzaria",
        "hamburgueria",
        "restaurante",
        "delivery",
        "cafeteria",
    ]):
        return "food"

    if any(k in n for k in [
        "barbearia",
        "salão",
        "beleza",
        "manicure",
    ]):
        return "beleza"

    if any(k in n for k in [
        "clinica",
        "dentista",
        "médico",
        "academia",
    ]):
        return "saude"

    if any(k in n for k in [
        "oficina",
        "mecanica",
        "auto",
    ]):
        return "automotivo"

    if any(k in n for k in [
        "curso",
        "escola",
        "faculdade",
    ]):
        return "educacao"

    if any(k in n for k in [
        "advocacia",
        "contabilidade",
        "imobiliaria",
    ]):
        return "servicos"

    return "varejo"

# ============================================================
# SCORE
# ============================================================


def calcular_score(lead: dict):
    score = 50

    try:
        rating = float(lead.get("rating") or 0)

        if rating >= 4.5:
            score += 20

        elif rating >= 4.0:
            score += 15

    except Exception:
        pass

    if lead.get("tem_site") == "false":
        score += 20

    ng = lead.get("nicho_group")

    if ng in ["food", "beleza"]:
        score += 10

    return max(0, min(100, score))



def calcular_priority(score: int):
    if score >= 75:
        return "quente"

    if score >= 50:
        return "morno"

    return "baixo"

# ============================================================
# EVOLUTION API
# ============================================================


def verificar_whatsapp(numeros: list):
    if not numeros:
        return None

    if DRY_RUN:
        return {
            re.sub(r"\D", "", n): True
            for n in numeros
        }

    numeros_puros = [
        re.sub(r"\D", "", n)
        for n in numeros
    ]

    payload = {
        "numbers": numeros_puros
    }

    url = f"{EVOLUTION_URL}/chat/whatsappNumbers/{requests.utils.quote(EVOLUTION_INST)}"

    headers = {
        "apikey": EVOLUTION_APIKEY,
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=90,
        )

        if not response.ok:
            log.error(f"Erro Evolution: {response.status_code}")
            return None

        data = response.json()

        resultado = {}

        for item in data:
            raw = str(item.get("number") or item.get("jid") or "")
            numero = re.sub(r"\D", "", raw)

            resultado[numero] = item.get("exists") is True

        return resultado

    except Exception as e:
        log.error(f"Falha Evolution API: {e}")
        return None

# ============================================================
# PREPARAR LOTE
# ============================================================


def preparar_lote(
    registros,
    cursor,
    nome_aba,
    numeros_existentes,
    numeros_processados,
):
    lote = []

    i = cursor

    while i < len(registros) and len(lote) < LOTE_SIZE:
        row = registros[i]

        i += 1

        numero = normalizar_numero(
            row.get("phoneNumber") or row.get("phone") or ""
        )

        if not numero:
            continue

        digits = re.sub(r"\D", "", numero)

        if digits in numeros_existentes:
            continue

        if digits in numeros_processados:
            continue

        nome = str(
            row.get("title") or row.get("nome") or ""
        ).strip()

        if not nome:
            continue

        nicho = str(
            row.get("type") or row.get("nicho") or "geral"
        ).strip().lower()

        website = str(row.get("website") or "").strip()

        lead = {
            "numero": numero,
            "nome": nome,
            "nicho": nicho,
            "nicho_group": nicho_group(nicho),
            "endereco": str(
                row.get("address") or row.get("endereco") or ""
            ).strip(),
            "rating": str(row.get("rating") or "").strip(),
            "tem_site": "true" if website else "false",
            "origem_aba": nome_aba,
        }

        lead["score"] = calcular_score(lead)
        lead["priority_level"] = calcular_priority(lead["score"])
        lead["whatsapp_confirmado"] = "pendente"
        lead["data_verificacao"] = datetime.now().strftime("%d/%m/%Y")

        lote.append(lead)

    return lote, i

# ============================================================
# GRAVAÇÃO SHEETS
# ============================================================


def _gravar_com_retry(planilha, nome_aba, linhas):
    retries = [5, 15, 30]

    for tentativa, espera in enumerate(retries):
        try:
            aba = planilha.worksheet(nome_aba)

            aba.append_rows(
                linhas,
                value_input_option="RAW"
            )

            log.info(f"{len(linhas)} gravados em {nome_aba}")
            return

        except Exception as e:
            log.error(f"Erro gravando {nome_aba}: {e}")

            if tentativa < len(retries) - 1:
                time.sleep(espera)
            else:
                raise



def gravar_leads_validados(planilha, leads):
    if not leads:
        return

    linhas = []

    for l in leads:
        linhas.append([
            l["numero"],
            l["nome"],
            l["nicho"],
            l["nicho_group"],
            l["endereco"],
            l["rating"],
            l["tem_site"],
            l["origem_aba"],
            l["score"],
            l["priority_level"],
            l["whatsapp_confirmado"],
            l["data_verificacao"],
        ])

    _gravar_com_retry(
        planilha,
        "leads_validados",
        linhas,
    )



def gravar_leads_sem_wpp(planilha, leads):
    if not leads:
        return

    linhas = []

    for l in leads:
        linhas.append([
            l["numero"],
            l["nome"],
            l["nicho"],
            l["origem_aba"],
            l["data_verificacao"],
        ])

    _gravar_com_retry(
        planilha,
        "leads_sem_wpp",
        linhas,
    )

# ============================================================
# MAIN
# ============================================================


def main():
    global _shutdown

    log.info("=" * 60)
    log.info("VisívelAgora — Qualificador v6")
    log.info("=" * 60)

    planilha = conectar_sheets()

    estado = carregar_estado()

    aba_index = estado["aba_index"]
    cursor = estado["cursor"]
    total_gravados = estado["total_gravados"]

    numeros_existentes = ler_numeros_validados(planilha)
    numeros_processados = carregar_processados()

    try:
        while aba_index < len(ABAS) and not _shutdown:
            nome_aba = ABAS[aba_index]

            log.info(f"Processando: {nome_aba}")

            registros = ler_aba(planilha, nome_aba)

            if not registros:
                aba_index += 1
                cursor = 0
                continue

            while cursor < len(registros) and not _shutdown:
                lote, novo_cursor = preparar_lote(
                    registros,
                    cursor,
                    nome_aba,
                    numeros_existentes,
                    numeros_processados,
                )

                cursor = novo_cursor

                if not lote:
                    continue

                resultado_api = verificar_whatsapp([
                    l["numero"]
                    for l in lote
                ])

                # API falhou
                if resultado_api is None:
                    log.warning("Lote ignorado por falha da API")
                    time.sleep(15)
                    continue

                com_wpp = []
                sem_wpp = []

                for lead in lote:
                    digits = re.sub(r"\D", "", lead["numero"])

                    resultado = resultado_api.get(digits)

                    if resultado is True:
                        lead["whatsapp_confirmado"] = "true"
                        com_wpp.append(lead)
                        numeros_existentes.add(digits)

                    elif resultado is False:
                        lead["whatsapp_confirmado"] = "false"
                        sem_wpp.append(lead)

                    else:
                        continue

                    numeros_processados.add(digits)

                if com_wpp:
                    gravar_leads_validados(
                        planilha,
                        com_wpp,
                    )

                    total_gravados += len(com_wpp)

                if sem_wpp:
                    gravar_leads_sem_wpp(
                        planilha,
                        sem_wpp,
                    )

                salvar_estado({
                    "aba_index": aba_index,
                    "cursor": cursor,
                    "total_gravados": total_gravados,
                })

                salvar_processados(numeros_processados)

                log.info(
                    f"Lote concluído | WPP={len(com_wpp)} | "
                    f"SEM_WPP={len(sem_wpp)} | "
                    f"TOTAL={total_gravados}"
                )

                delay = DELAY_ENTRE_LOTES + random.randint(0, DELAY_JITTER)

                log.info(f"Aguardando {delay}s...")

                time.sleep(delay)

            aba_index += 1
            cursor = 0

    finally:
        salvar_processados(numeros_processados)

        salvar_estado({
            "aba_index": aba_index,
            "cursor": cursor,
            "total_gravados": total_gravados,
        })

        relatorio = {
            "finalizado_em": datetime.now().strftime("%d/%m/%Y %H:%M:%S"),
            "total_gravados": total_gravados,
            "processados": len(numeros_processados),
        }

        atomic_write_json(
            RELATORIO_FILE,
            relatorio,
        )

        log.info("Processamento encerrado")


if __name__ == "__main__":
    main()
