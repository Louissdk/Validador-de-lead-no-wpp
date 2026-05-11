"""
VisívelAgora — Qualificador de Leads v8
========================================
Combina o melhor do v5 e v7:

DO V7 (novidades):
  ✅ atomic_write_json — sem corrupção em crash
  ✅ processados.json — rastreia com WPP e sem WPP
  ✅ DATA_DIR /app/data/ — arquivos organizados
  ✅ Cursor só avança APÓS gravação bem-sucedida
  ✅ Detecção explícita de 428 Connection Closed
  ✅ FORCAR_RESET flag no topo
  ✅ leads_sem_wpp só grava quando exists=False genuíno

DO V5 (mantidos e corrigidos):
  ✅ nicho_group completo (60+ palavras-chave)
  ✅ normalizar_numero com validação de celular brasileiro
  ✅ Delay 25-40s anti-ban
  ✅ Retry na gravação do Sheets (3x com backoff)
  ✅ Validação de conexões no início
  ✅ vistos_sessao — sem duplicata no mesmo lote
  ✅ leads_raw como ÚLTIMA aba (auditoria)
  ✅ Log de conclusão por aba
  ✅ Relatório final completo

CORREÇÕES NOVAS NO V8:
  ✅ tem_site usa len(website.strip()) > 3
  ✅ calcular_score sem bare except
  ✅ processados.json como set de digits (sem timestamps) — menor em memória
  ✅ API pausa 2min e retenta — não pula lote
  ✅ Log de pré-análise por aba
"""

import json
import re
import time
import signal
import logging
import random
from pathlib import Path
from datetime import datetime

import requests
import gspread
from google.oauth2.service_account import Credentials

# ═══════════════════════════════════════════════════════
#  CONFIGURAÇÕES
# ═══════════════════════════════════════════════════════

SPREADSHEET_ID   = "1yiZQnUqumPgVRv9DbF3aQzgwbJt6L3RlaucvesXKxSc"
EVOLUTION_URL    = "https://n8n-evolution-api.6laxw2.easypanel.host"
EVOLUTION_APIKEY = "429683C4C977415CAAFCCE10F7D57E11"
EVOLUTION_INST   = "Visivel agora"

GOOGLE_CREDS_FILE = "credentials.json"

DATA_DIR = Path("/app/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

ESTADO_FILE     = DATA_DIR / "estado.json"
PROCESSADOS_FILE = DATA_DIR / "processados.json"
RELATORIO_FILE  = DATA_DIR / "relatorio_final.json"
LOG_FILE        = Path("/app/qualificar_leads.log")

LOTE_SIZE          = 5
DELAY_MIN          = 60   # 1 minuto entre lotes
DELAY_MAX          = 90   # até 1.5 minutos entre lotes
DELAY_API_FALHOU   = 300  # 5 minutos quando API cai

DRY_RUN      = False
FORCAR_RESET = False  # mude para True para recomeçar do zero

# 15 abas + leads_raw como auditoria FINAL
ABAS = [
    "leads_raw_1k",  "leads_raw_2k",  "leads_raw_3k",
    "leads_raw_4k",  "leads_raw_5k",  "leads_raw_6k",
    "leads_raw_7k",  "leads_raw_8k",  "leads_raw_9k",
    "leads_raw_10k", "leads_raw_11k", "leads_raw_12k",
    "leads_raw_13k", "leads_raw_14k", "leads_raw_15k",
    "leads_raw",  # auditoria — pega o que ficou para trás
]

# ═══════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
    ],
)
log = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════
#  GRACEFUL SHUTDOWN
# ═══════════════════════════════════════════════════════

_shutdown = False

def handle_signal(signum, frame):
    global _shutdown
    _shutdown = True
    log.warning("⚠️  Sinal de encerramento recebido. Finalizando lote atual...")

signal.signal(signal.SIGINT,  handle_signal)
signal.signal(signal.SIGTERM, handle_signal)

# ═══════════════════════════════════════════════════════
#  ATOMIC WRITE — sem corrupção em crash
# ═══════════════════════════════════════════════════════

def atomic_write_json(path: Path, data):
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)

# ═══════════════════════════════════════════════════════
#  ESTADO
# ═══════════════════════════════════════════════════════

def carregar_estado() -> dict:
    if not ESTADO_FILE.exists():
        return {"aba_index": 0, "cursor": 0, "total_gravados": 0}
    try:
        with open(ESTADO_FILE, "r", encoding="utf-8") as f:
            estado = json.load(f)
        aba_nome = ABAS[min(estado["aba_index"], len(ABAS) - 1)]
        log.info(f"Progresso encontrado → aba: {aba_nome} | gravados: {estado['total_gravados']}")
        if FORCAR_RESET:
            log.warning("FORCAR_RESET=True — reiniciando do zero.")
            return {"aba_index": 0, "cursor": 0, "total_gravados": 0}
        log.info("Continuando de onde parou. (Para resetar: FORCAR_RESET=True no topo do arquivo)")
        return estado
    except Exception as e:
        log.error(f"Erro ao carregar estado.json: {e}. Reiniciando do zero.")
        return {"aba_index": 0, "cursor": 0, "total_gravados": 0}

def salvar_estado(estado: dict):
    atomic_write_json(ESTADO_FILE, estado)

# ═══════════════════════════════════════════════════════
#  PROCESSADOS — set de números já verificados (com ou sem WPP)
# ═══════════════════════════════════════════════════════

def carregar_processados() -> set:
    if not PROCESSADOS_FILE.exists():
        log.info("processados.json não encontrado. Iniciando rastreamento do zero.")
        return set()
    try:
        with open(PROCESSADOS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Aceita tanto lista (v8) quanto dict (v7 antigo)
        if isinstance(data, dict):
            return set(data.keys())
        return set(data)
    except Exception as e:
        log.error(f"Erro ao carregar processados.json: {e}. Reiniciando.")
        return set()

def salvar_processados(processados: set):
    atomic_write_json(PROCESSADOS_FILE, list(processados))

# ═══════════════════════════════════════════════════════
#  GOOGLE SHEETS
# ═══════════════════════════════════════════════════════

def conectar_sheets() -> gspread.Spreadsheet:
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(GOOGLE_CREDS_FILE, scopes=scopes)
    client = gspread.authorize(creds)
    planilha = client.open_by_key(SPREADSHEET_ID)
    log.info(f"✅ Conectado ao Google Sheets: {planilha.title}")
    return planilha


def validar_conexoes(planilha: gspread.Spreadsheet) -> bool:
    log.info("Validando conexões...")
    try:
        planilha.worksheet("leads_raw_1k").row_values(1)
        log.info("✅ Google Sheets: OK")
    except Exception as e:
        log.error(f"❌ Google Sheets: {e}")
        return False
    if not DRY_RUN:
        try:
            url = f"{EVOLUTION_URL}/chat/whatsappNumbers/{requests.utils.quote(EVOLUTION_INST)}"
            headers = {"apikey": EVOLUTION_APIKEY, "Content-Type": "application/json"}
            resp = requests.post(url, headers=headers, json={"numbers": []}, timeout=10)
            if resp.status_code in [200, 400]:
                log.info("✅ Evolution API: OK")
            else:
                log.error(f"❌ Evolution API: {resp.status_code} — {resp.text[:200]}")
                return False
        except Exception as e:
            log.error(f"❌ Evolution API: {e}")
            return False
    return True


def ler_aba(planilha: gspread.Spreadsheet, nome: str) -> list[dict]:
    try:
        aba = planilha.worksheet(nome)
        registros = aba.get_all_records(empty2zero=False, head=1)
        log.info(f"Aba '{nome}': {len(registros)} linhas lidas.")
        return registros
    except gspread.exceptions.WorksheetNotFound:
        log.warning(f"Aba '{nome}' não encontrada. Pulando.")
        return []
    except Exception as e:
        log.error(f"Erro ao ler '{nome}': {e}")
        return []


def ler_numeros_validados(planilha: gspread.Spreadsheet) -> set:
    try:
        aba = planilha.worksheet("leads_validados")
        valores = aba.col_values(1)
        numeros = set(re.sub(r"\D", "", v) for v in valores[1:] if v)
        log.info(f"leads_validados: {len(numeros)} números com WPP confirmado.")
        return numeros
    except Exception as e:
        log.warning(f"Erro ao ler leads_validados: {e}. Assumindo vazio.")
        return set()


def gravar_linhas(planilha: gspread.Spreadsheet, nome_aba: str, linhas: list):
    """Grava com retry 3x e backoff exponencial."""
    if not linhas or DRY_RUN:
        if DRY_RUN and linhas:
            log.info(f"[DRY_RUN] Gravaria {len(linhas)} linhas em '{nome_aba}'.")
        return
    esperas = [5, 15, 30]
    for tentativa, espera in enumerate(esperas):
        try:
            aba = planilha.worksheet(nome_aba)
            aba.append_rows(linhas, value_input_option="RAW")
            log.info(f"✅ Gravados {len(linhas)} registros em '{nome_aba}'.")
            return
        except Exception as e:
            log.error(f"Erro ao gravar em '{nome_aba}' (tentativa {tentativa+1}/3): {e}")
            if tentativa < len(esperas) - 1:
                log.info(f"Retry em {espera}s...")
                time.sleep(espera)
            else:
                log.error(f"Falha definitiva ao gravar em '{nome_aba}'.")
                raise

# ═══════════════════════════════════════════════════════
#  NORMALIZAÇÃO DE TELEFONE
# ═══════════════════════════════════════════════════════

def normalizar_numero(raw) -> str | None:
    p = re.sub(r"\D", "", str(raw or ""))
    if not p:
        return None
    if p.startswith("0055"):
        p = p[4:]
    elif p.startswith("55"):
        p = p[2:]
    if len(p) < 10 or len(p) > 11:
        return None
    sufixo = p[2:]  # remove DDD
    # Fixo comercial — não tem WhatsApp
    if len(p) == 10 and sufixo[0] in ["2", "3", "4", "5"]:
        return None
    # Celular deve começar com 9
    if len(p) == 11 and sufixo[0] != "9":
        return None
    return "55" + p

# ═══════════════════════════════════════════════════════
#  NICHO GROUP — 60+ palavras-chave, ordem importa
#  (saude antes de entretenimento por causa de "laser")
# ═══════════════════════════════════════════════════════

def nicho_group(nicho: str) -> str:
    n = (nicho or "").lower()

    if any(k in n for k in [
        "pizzaria","restaurante","lanche","hamburgue","hamburgueria","esfiaria",
        "churrascaria","japonesa","árabe","arabe","food truck","cafeteria","padaria",
        "confeitaria","açaí","acai","sorvete","sorveteria","salgado","doceria",
        "lanchonete","delivery","marmita","cozinha","bistrô","bistro","quilo",
        "buffet","cantina",
    ]):
        return "food"

    if any(k in n for k in [
        "barbearia","beleza","estetica","estética","tatuagem","micropigmentacao",
        "depilacao","depilação","spa","salão","salao","manicure","cabeleireiro",
        "studio","nail","sobrancelha","cabelo","esteticista","bronzeamento",
        "podologia","podólogo",
    ]):
        return "beleza"

    if any(k in n for k in [
        "odontologia","dentista","clinica","clínica","psicólogo","psicologo",
        "fisioterapia","nutricionista","farmacia","farmácia","academia",
        "médico","medico","hospital","laboratório","laboratorio","exame",
        "cirurgia","ortopedia","pediatria","cardiologia","dermatologia",
        "psiquiatria","terapia","terapeuta","veterinário","veterinario",
        "pet","acupuntura","homeopatia","reabilitação","laser",
    ]):
        return "saude"

    if any(k in n for k in [
        "concessionaria","concessionária","mecanica","mecânica","borracharia",
        "lava jato","funilaria","guincho","oficina","pneus","auto","autopeças",
        "autopecas","troca de óleo","insulfilm","som automotivo","despachante",
    ]):
        return "automotivo"

    if any(k in n for k in [
        "escola","curso","faculdade","creche","idiomas","reforço","reforco",
        "colégio","colegio","ensino","pré-escola","berçário","bercario",
        "inglês","ingles","música","musica","dança","danca","arte",
    ]):
        return "educacao"

    if any(k in n for k in [
        "serralheria","eletricista","encanador","dedetizadora","imobiliaria",
        "imobiliária","advocacia","contabilidade","segurança","seguranca",
        "limpeza","zeladoria","reformas","pintura","marido de aluguel",
        "desentupimento","ar condicionado","chaveiro","vidraçaria","vidracaria",
        "gráfica","grafica","fotografia","fotografo","cartório","cartorio",
    ]):
        return "servicos"

    if any(k in n for k in [
        "bar","boate","clube","shows","evento","festa","buffet infantil",
        "salão de festas","karaoke","karaokê","casa de festas","brinquedoteca",
    ]):
        return "entretenimento"

    return "varejo"

# ═══════════════════════════════════════════════════════
#  SCORE E PRIORITY
# ═══════════════════════════════════════════════════════

def calcular_score(lead: dict) -> int:
    score = 50

    try:
        rating = float(lead.get("rating") or 0)
        if rating >= 4.7:        score += 25
        elif rating >= 4.5:      score += 20
        elif rating >= 4.0:      score += 15
        elif rating >= 3.5:      score += 8
        elif rating == 0.0:      score -= 5   # sem rating — incerto
        elif rating < 3.0:       score -= 15
    except (ValueError, TypeError):
        score -= 5  # rating inválido

    if lead.get("tem_site") == "false":   score += 20  # público-alvo!
    elif lead.get("tem_site") == "true":  score -= 5

    ng = lead.get("nicho_group", "varejo")
    if ng in ["food", "beleza"]:             score += 15
    elif ng in ["saude"]:                    score += 10
    elif ng in ["varejo"]:                   score += 5
    elif ng in ["automotivo", "servicos"]:   score += 3

    return max(0, min(100, score))


def calcular_priority(score: int) -> str:
    if score >= 75: return "quente"
    if score >= 50: return "morno"
    return "baixo"

# ═══════════════════════════════════════════════════════
#  EVOLUTION API
#  Retorna: dict {digits: bool} | None (API indisponível)
#  None = não descartar leads, aguardar e retomar
# ═══════════════════════════════════════════════════════

def verificar_whatsapp(numeros: list[str]) -> dict[str, bool] | None:
    if not numeros:
        return {}
    if DRY_RUN:
        return {re.sub(r"[^0-9]", "", n): True for n in numeros}

    numeros_puros = [re.sub(r"[^0-9]", "", n) for n in numeros]
    payload = {"numbers": numeros_puros}
    url = f"{EVOLUTION_URL}/chat/whatsappNumbers/{requests.utils.quote(EVOLUTION_INST)}"
    headers = {"apikey": EVOLUTION_APIKEY, "Content-Type": "application/json"}

    esperas = [5, 15, 30]
    resp = None

    for tentativa, espera in enumerate(esperas):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=30)

            # 428 = WhatsApp desconectado — não adianta retry
            if resp.status_code == 428:
                log.error("❌ WhatsApp desconectado (428). Reconecte na Evolution API!")
                return None

            if resp.ok:
                break

            log.warning(f"Evolution API {resp.status_code} (tentativa {tentativa+1}/3): {resp.text[:150]}")
            if tentativa < len(esperas) - 1:
                time.sleep(espera)
            else:
                return None

        except requests.exceptions.RequestException as e:
            log.error(f"Evolution API timeout/erro (tentativa {tentativa+1}/3): {e}")
            if tentativa < len(esperas) - 1:
                time.sleep(espera)
            else:
                return None

    if resp is None or not resp.ok:
        return None

    try:
        data = resp.json()
    except Exception:
        log.error("Evolution API retornou JSON inválido.")
        return None

    resultado = {}
    for item in (data if isinstance(data, list) else []):
        if not isinstance(item, dict):
            continue
        raw = str(item.get("number") or item.get("jid") or "")
        num = re.sub(r"[^0-9]", "", raw)
        if num:
            resultado[num] = item.get("exists") is True

    com_wpp = sum(1 for v in resultado.values() if v)
    log.info(f"Evolution API: {com_wpp}/{len(numeros)} com WPP.")
    return resultado

# ═══════════════════════════════════════════════════════
#  PRÉ-ANÁLISE DA ABA
# ═══════════════════════════════════════════════════════

def pre_analise(registros: list[dict], numeros_existentes: set, processados: set) -> dict:
    total = len(registros)
    ja_wpp = 0
    ja_verificado = 0
    sem_numero = 0
    novos = 0

    amostra = []

    for row in registros:
        raw = row.get("phoneNumber") or row.get("phone") or ""
        numero = normalizar_numero(raw)
        if not numero:
            sem_numero += 1
            if len(amostra) < 5:
                amostra.append(f"'{raw}' → 'None'")
            continue
        digits = re.sub(r"\D", "", numero)
        if digits in numeros_existentes:
            ja_wpp += 1
        elif digits in processados:
            ja_verificado += 1
        else:
            novos += 1
            if len(amostra) < 5:
                amostra.append(f"'{raw}' → '{numero}'")

    log.info(f"  📊 Pré-análise: Total={total} | Novos={novos} | Já com WPP={ja_wpp} | Já verificados (sem WPP)={ja_verificado} | Sem número válido={sem_numero}")
    log.info(f"  ⏱️  Estimativa: ~{(novos // LOTE_SIZE) + 1} lotes reais ({novos} leads novos)")
    if amostra:
        log.info(f"  🔍 Amostra de números (raw → normalizado): {' | '.join(amostra)}")

    return {"novos": novos, "ja_wpp": ja_wpp, "ja_verificado": ja_verificado, "sem_numero": sem_numero}

# ═══════════════════════════════════════════════════════
#  RELATÓRIO FINAL
# ═══════════════════════════════════════════════════════

def salvar_relatorio(stats: dict):
    atomic_write_json(RELATORIO_FILE, stats)
    log.info(f"Relatório salvo em {RELATORIO_FILE}")

# ═══════════════════════════════════════════════════════
#  LOOP PRINCIPAL
# ═══════════════════════════════════════════════════════

def main():
    global _shutdown

    log.info("=" * 60)
    log.info("VisívelAgora — Qualificador de Leads v8")
    log.info(f"Início: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    log.info(f"Abas: {len(ABAS)} | Lotes de {LOTE_SIZE} | Delay {DELAY_MIN}-{DELAY_MAX}s | DRY_RUN={DRY_RUN}")
    log.info("=" * 60)

    if not Path(GOOGLE_CREDS_FILE).exists():
        log.error(f"'{GOOGLE_CREDS_FILE}' não encontrado!")
        return

    try:
        with open(GOOGLE_CREDS_FILE) as f:
            json.load(f)
    except Exception as e:
        log.error(f"credentials.json inválido: {e}")
        return

    planilha = conectar_sheets()

    if not validar_conexoes(planilha):
        log.error("Validação falhou. Encerrando.")
        return

    estado         = carregar_estado()
    aba_index      = estado["aba_index"]
    cursor         = estado["cursor"]
    total_gravados = estado["total_gravados"]

    if aba_index >= len(ABAS):
        log.info("✅ Todas as abas já foram processadas. Nada a fazer.")
        return

    log.info("Carregando dados para deduplicação...")
    numeros_existentes = ler_numeros_validados(planilha)
    processados        = carregar_processados()
    vistos_sessao      = set()  # duplicatas dentro da sessão atual

    # Estatísticas da sessão
    data_inicio      = datetime.now()
    total_com_wpp    = 0
    total_sem_wpp    = 0
    gravados_sessao  = 0
    tempos_lote      = []
    por_nicho        = {k: 0 for k in ["food","beleza","saude","varejo","automotivo","educacao","servicos","entretenimento"]}

    try:
        while aba_index < len(ABAS) and not _shutdown:
            nome_aba = ABAS[aba_index]
            log.info(f"\n{'─'*50}")
            log.info(f"📂 Aba {aba_index+1}/{len(ABAS)}: {nome_aba} (cursor={cursor})")

            registros = ler_aba(planilha, nome_aba)

            if not registros:
                aba_index += 1
                cursor = 0
                salvar_estado({"aba_index": aba_index, "cursor": cursor, "total_gravados": total_gravados})
                continue

            # Pré-análise antes de processar
            analise = pre_analise(registros, numeros_existentes, processados)
            total_lotes_aba = (analise["novos"] // LOTE_SIZE) + 1
            num_lote_aba    = 0
            wpp_aba         = 0

            while cursor < len(registros) and not _shutdown:
                t_inicio      = time.time()
                num_lote_aba += 1

                # ─── PREPARA LOTE ──────────────────────────────
                lote          = []
                novo_cursor   = cursor
                pulados_log   = {"ja_wpp": 0, "ja_verificado": 0, "sem_numero": 0, "sem_nome": 0}

                while novo_cursor < len(registros) and len(lote) < LOTE_SIZE:
                    row         = registros[novo_cursor]
                    novo_cursor += 1

                    numero = normalizar_numero(row.get("phoneNumber") or row.get("phone") or "")
                    if not numero:
                        pulados_log["sem_numero"] += 1
                        continue

                    digits = re.sub(r"\D", "", numero)

                    if digits in numeros_existentes:
                        pulados_log["ja_wpp"] += 1
                        continue
                    if digits in processados:
                        pulados_log["ja_verificado"] += 1
                        continue
                    if digits in vistos_sessao:
                        continue

                    nome = str(row.get("title") or row.get("nome") or "").strip()
                    if not nome:
                        pulados_log["sem_nome"] += 1
                        continue

                    nicho   = str(row.get("type") or row.get("nicho") or "geral").lower().strip()
                    website = str(row.get("website") or "").strip()
                    ng      = nicho_group(nicho)
                    ts      = "true" if len(website.strip()) > 3 else "false"

                    lead = {
                        "numero":      numero,
                        "nome":        nome,
                        "nicho":       nicho,
                        "nicho_group": ng,
                        "endereco":    str(row.get("address") or row.get("endereco") or "").strip(),
                        "rating":      str(row.get("rating") or "").strip(),
                        "tem_site":    ts,
                        "origem_aba":  nome_aba,
                    }
                    lead["score"]               = calcular_score(lead)
                    lead["priority_level"]      = calcular_priority(lead["score"])
                    lead["whatsapp_confirmado"] = "true"
                    lead["data_verificacao"]    = datetime.now().strftime("%d/%m/%Y")

                    vistos_sessao.add(digits)
                    lote.append(lead)

                total_pulados = sum(pulados_log.values())
                if total_pulados > 0:
                    log.info(f"  ↩️  Pulados nesse trecho: {total_pulados} (já com WPP={pulados_log['ja_wpp']} | já verificado={pulados_log['ja_verificado']} | sem número={pulados_log['sem_numero']} | sem nome={pulados_log['sem_nome']})")

                if not lote:
                    cursor = novo_cursor
                    salvar_estado({"aba_index": aba_index, "cursor": cursor, "total_gravados": total_gravados})
                    continue

                # ─── VERIFICA WHATSAPP ─────────────────────────
                wpp_map = verificar_whatsapp([l["numero"] for l in lote])

                if wpp_map is None:
                    # API indisponível — NÃO avança cursor, NÃO descarta leads
                    log.warning(f"⚠️  Evolution API indisponível. Aguardando {DELAY_API_FALHOU}s antes de retomar...")
                    log.warning("   Verifique se o WhatsApp está conectado na Evolution API!")
                    # Remove do vistos_sessao para reprocessar
                    for lead in lote:
                        vistos_sessao.discard(re.sub(r"\D", "", lead["numero"]))
                    time.sleep(DELAY_API_FALHOU)
                    continue  # tenta o mesmo lote de novo

                # ─── SEPARA COM WPP E SEM WPP ─────────────────
                linhas_wpp = []
                linhas_sem = []

                for lead in lote:
                    digits = re.sub(r"\D", "", lead["numero"])
                    status = wpp_map.get(digits)

                    if status is True:
                        linhas_wpp.append([
                            lead["numero"], lead["nome"], lead["nicho"], lead["nicho_group"],
                            lead["endereco"], lead["rating"], lead["tem_site"], lead["origem_aba"],
                            lead["score"], lead["priority_level"], lead["whatsapp_confirmado"], lead["data_verificacao"],
                        ])
                        numeros_existentes.add(digits)
                        processados.add(digits)
                        total_com_wpp  += 1
                        gravados_sessao += 1
                        por_nicho[lead["nicho_group"]] = por_nicho.get(lead["nicho_group"], 0) + 1
                        wpp_aba += 1

                    elif status is False:
                        linhas_sem.append([
                            lead["numero"], lead["nome"], lead["nicho"], lead["origem_aba"], lead["data_verificacao"],
                        ])
                        processados.add(digits)
                        total_sem_wpp += 1

                    # status is None = número não veio na resposta (raro) — não grava nem descarta

                # ─── GRAVAÇÃO — cursor só avança APÓS sucesso ──
                try:
                    if linhas_wpp:
                        gravar_linhas(planilha, "leads_validados", linhas_wpp)
                        total_gravados += len(linhas_wpp)

                    if linhas_sem:
                        gravar_linhas(planilha, "leads_sem_wpp", linhas_sem)

                    salvar_processados(processados)

                    # Só agora avança o cursor
                    cursor = novo_cursor
                    salvar_estado({"aba_index": aba_index, "cursor": cursor, "total_gravados": total_gravados})

                except Exception as e:
                    log.error(f"Falha ao gravar lote. Cursor NÃO avançado. Erro: {e}")
                    # Remove do vistos_sessao para reprocessar
                    for lead in lote:
                        vistos_sessao.discard(re.sub(r"\D", "", lead["numero"]))
                    time.sleep(30)
                    continue

                # ─── LOG DE PROGRESSO ──────────────────────────
                t_lote = time.time() - t_inicio
                tempos_lote.append(t_lote)
                media_t = sum(tempos_lote) / len(tempos_lote)
                est_min = int(((total_lotes_aba - num_lote_aba) * media_t) / 60)
                pct_aba = round(cursor / len(registros) * 100)
                taxa    = round(total_com_wpp / max(1, total_com_wpp + total_sem_wpp) * 100, 1)

                log.info(
                    f"[{nome_aba}] Lote {num_lote_aba} | "
                    f"WPP: {len(linhas_wpp)}/{len(lote)} ({round(len(linhas_wpp)/len(lote)*100)}%) | "
                    f"Sessão: {gravados_sessao} | Total geral: {total_gravados} | "
                    f"Aba: {pct_aba}% | Taxa geral: {taxa}% | ~{est_min}min restantes"
                )

                delay = random.randint(DELAY_MIN, DELAY_MAX)
                log.info(f"Aguardando {delay}s...")
                time.sleep(delay)

            # Aba concluída
            if not _shutdown:
                log.info(f"✅ Aba '{nome_aba}' concluída — WPP encontrados: {wpp_aba} | Sem WPP: {total_sem_wpp}")
                aba_index += 1
                cursor = 0
                salvar_estado({"aba_index": aba_index, "cursor": cursor, "total_gravados": total_gravados})

    finally:
        data_fim = datetime.now()
        duracao  = int((data_fim - data_inicio).total_seconds() / 60)
        total_verificados = total_com_wpp + total_sem_wpp
        taxa_wpp = f"{round(total_com_wpp / max(1, total_verificados) * 100, 1)}%"

        relatorio = {
            "data_inicio":      data_inicio.strftime("%d/%m/%Y %H:%M:%S"),
            "data_fim":         data_fim.strftime("%d/%m/%Y %H:%M:%S"),
            "duracao_minutos":  duracao,
            "total_verificados": total_verificados,
            "total_com_wpp":    total_com_wpp,
            "total_sem_wpp":    total_sem_wpp,
            "total_gravados":   total_gravados,
            "taxa_wpp":         taxa_wpp,
            "por_nicho_group":  por_nicho,
        }
        salvar_relatorio(relatorio)

        if _shutdown:
            log.info("Script encerrado pelo usuário. Estado salvo — rode novamente para retomar.")
        else:
            log.info("\n" + "=" * 60)
            log.info("🎉 PROCESSAMENTO CONCLUÍDO!")
            log.info(f"Total gravados em leads_validados: {total_gravados}")
            log.info(f"Taxa WPP: {taxa_wpp}")
            log.info("=" * 60)


if __name__ == "__main__":
    main()
