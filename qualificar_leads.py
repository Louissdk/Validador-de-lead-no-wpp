"""
VisívelAgora — Qualificador de Leads v5
========================================
CORREÇÕES v5:
  [BUG#1] Leads sem WPP agora são rastreados entre sessões via processados.json
          → Evita re-chamadas desnecessárias na Evolution API
  [BUG#2] preparar_lote agora loga cada lead pulado por dedup com motivo
          → Visibilidade total do que está sendo descartado
  [BUG#3] Estimativa de tempo usa média real de lotes válidos, não total do arquivo
          → Progresso preciso

MELHORIAS v5:
  - Pré-análise por aba antes de processar (mostra novos vs já validados vs sem WPP)
  - Amostra de números brutos no 1º lote para debug de formatação
  - % de conclusão real por aba e global
  - Aba 'leads_sem_wpp' no Sheets para auditoria completa
  - Relatório final expandido com breakdown por aba
  - Shutdown graceful salva estado ANTES do finally (segurança extra)
  - Detecção de aba completamente vazia de novos leads (pula sem delay)
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

# ─────────────────────────────────────────────
#  CONFIGURAÇÕES
# ─────────────────────────────────────────────

SPREADSHEET_ID    = "1yiZQnUqumPgVRv9DbF3aQzgwbJt6L3RlaucvesXKxSc"
EVOLUTION_URL     = "https://n8n-evolution-api.6laxw2.easypanel.host"
EVOLUTION_APIKEY  = "429683C4C977415CAAFCCE10F7D57E11"
EVOLUTION_INST    = "Visivel agora"
GOOGLE_CREDS_FILE = "credentials.json"
ESTADO_FILE       = "estado.json"
PROCESSADOS_FILE  = "processados.json"   # [NOVO v5] rastreia todos os números já verificados
RELATORIO_FILE    = "relatorio_final.json"

LOTE_SIZE         = 10
DELAY_ENTRE_LOTES = 30
DELAY_JITTER      = 10

DRY_RUN = False

ABAS = [
    "leads_raw_1k",  "leads_raw_2k",  "leads_raw_3k",
    "leads_raw_4k",  "leads_raw_5k",  "leads_raw_6k",
    "leads_raw_7k",  "leads_raw_8k",  "leads_raw_9k",
    "leads_raw_10k", "leads_raw_11k", "leads_raw_12k",
    "leads_raw_13k", "leads_raw_14k", "leads_raw_15k",
]

# ─────────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("qualificar_leads.log", encoding="utf-8"),
    ]
)
log = logging.getLogger(__name__)

# ─────────────────────────────────────────────
#  GRACEFUL SHUTDOWN
# ─────────────────────────────────────────────

_shutdown = False

def _handle_signal(signum, frame):
    global _shutdown
    log.info("⚠️  Sinal de encerramento recebido. Finalizando após o lote atual...")
    _shutdown = True

signal.signal(signal.SIGINT,  _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)

# ─────────────────────────────────────────────
#  ESTADO + PROCESSADOS
# ─────────────────────────────────────────────

def carregar_estado() -> dict:
    if Path(ESTADO_FILE).exists():
        with open(ESTADO_FILE, "r", encoding="utf-8") as f:
            estado = json.load(f)
        aba_nome = ABAS[min(estado["aba_index"], len(ABAS)-1)]
        log.info(f"Retomando: aba={aba_nome}, cursor={estado['cursor']}, gravados={estado['total_gravados']}")
        return estado
    log.info("Nenhum estado salvo. Começando do zero.")
    return {"aba_index": 0, "cursor": 0, "total_gravados": 0}


def salvar_estado(estado: dict):
    with open(ESTADO_FILE, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)


def carregar_processados() -> set:
    """
    [NOVO v5 — FIX BUG#1]
    Carrega todos os números já verificados pela API (com OU sem WPP).
    Evita re-chamar a Evolution API para números que já foram verificados.
    """
    if Path(PROCESSADOS_FILE).exists():
        with open(PROCESSADOS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        numeros = set(data.get("numeros", []))
        log.info(f"processados.json: {len(numeros)} números já verificados (com e sem WPP).")
        return numeros
    log.info("processados.json não encontrado. Iniciando rastreamento do zero.")
    return set()


def salvar_processados(numeros: set):
    """Persiste o set de processados em disco."""
    with open(PROCESSADOS_FILE, "w", encoding="utf-8") as f:
        json.dump({"numeros": list(numeros), "atualizado_em": datetime.now().isoformat()}, f)

# ─────────────────────────────────────────────
#  GOOGLE SHEETS
# ─────────────────────────────────────────────

def conectar_sheets() -> gspread.Spreadsheet:
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file(GOOGLE_CREDS_FILE, scopes=scopes)
    client = gspread.authorize(creds)
    planilha = client.open_by_key(SPREADSHEET_ID)
    log.info("Conectado ao Google Sheets.")
    return planilha


def ler_aba(planilha: gspread.Spreadsheet, nome_aba: str) -> list[dict]:
    try:
        aba = planilha.worksheet(nome_aba)
        registros = aba.get_all_records(empty2zero=False, head=1)
        log.info(f"Aba '{nome_aba}': {len(registros)} linhas lidas.")
        return registros
    except gspread.exceptions.WorksheetNotFound:
        log.warning(f"Aba '{nome_aba}' não encontrada. Pulando.")
        return []
    except Exception as e:
        log.error(f"Erro ao ler aba '{nome_aba}': {e}")
        return []


def ler_numeros_validados(planilha: gspread.Spreadsheet) -> set:
    """Lê coluna A de leads_validados para deduplicação em memória."""
    try:
        aba = planilha.worksheet("leads_validados")
        valores = aba.col_values(1)
        numeros = set(
            re.sub(r"\D", "", v)
            for v in valores[1:]
            if v and re.sub(r"\D", "", v)
        )
        log.info(f"leads_validados: {len(numeros)} números com WPP confirmado.")
        return numeros
    except Exception as e:
        log.warning(f"Erro ao ler leads_validados: {e}. Assumindo vazio.")
        return set()


def gravar_leads_validados(planilha: gspread.Spreadsheet, leads: list[dict]):
    if not leads:
        return
    if DRY_RUN:
        log.info(f"[DRY_RUN] Gravaria {len(leads)} leads em leads_validados.")
        return

    linhas = [
        [
            l["numero"], l["nome"], l["nicho"], l["nicho_group"],
            l["endereco"], l["rating"], l["tem_site"], l["origem_aba"],
            l["score"], l["priority_level"], l["whatsapp_confirmado"], l["data_verificacao"],
        ]
        for l in leads
    ]
    _gravar_com_retry(planilha, "leads_validados", linhas)


def gravar_leads_sem_wpp(planilha: gspread.Spreadsheet, leads: list[dict]):
    """
    [NOVO v5 — FIX BUG#1]
    Grava leads verificados mas SEM WhatsApp em aba separada para auditoria.
    Isso permite reprocessar essa lista futuramente se necessário.
    """
    if not leads or DRY_RUN:
        return
    linhas = [
        [l["numero"], l["nome"], l["nicho"], l["origem_aba"], l["data_verificacao"]]
        for l in leads
    ]
    try:
        _gravar_com_retry(planilha, "leads_sem_wpp", linhas)
    except Exception as e:
        log.warning(f"Não foi possível gravar em leads_sem_wpp: {e}. Continuando...")


def _gravar_com_retry(planilha: gspread.Spreadsheet, nome_aba: str, linhas: list):
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

# ─────────────────────────────────────────────
#  VALIDAÇÃO INICIAL
# ─────────────────────────────────────────────

def validar_conexoes(planilha: gspread.Spreadsheet) -> bool:
    log.info("Validando conexões...")
    try:
        aba = planilha.worksheet("leads_raw_1k")
        aba.row_values(1)
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
                log.error(f"❌ Evolution API: status {resp.status_code}")
                return False
        except Exception as e:
            log.error(f"❌ Evolution API: {e}")
            return False

    return True

# ─────────────────────────────────────────────
#  NORMALIZAÇÃO DE TELEFONE
# ─────────────────────────────────────────────

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
    # [v5.1] Filtro de fixo removido — empresas usam WhatsApp Business em VoIP/fixo
    return "55" + p

# ─────────────────────────────────────────────
#  NICHO GROUP
# ─────────────────────────────────────────────

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
        "salao de beleza","studio","nail","sobrancelha","cabelo","esteticista",
        "bronzeamento","podologia","podólogo",
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
        "autopecas","troca de óleo","elétrica automotiva","insulfilm",
        "som automotivo","blindagem","despachante",
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
        "vigilância","limpeza","zeladoria","reformas","pintura",
        "marido de aluguel","desentupimento","ar condicionado","refrigeração",
        "chaveiro","vidraçaria","vidracaria","gráfica","grafica","fotografia",
        "fotografo","cartório","cartorio",
    ]):
        return "servicos"

    if any(k in n for k in [
        "bar","boate","clube","shows","evento","festa","buffet infantil",
        "salão de festas","karaoke","karaokê","espaço para eventos",
        "casa de festas","brinquedoteca",
    ]):
        return "entretenimento"

    return "varejo"

# ─────────────────────────────────────────────
#  SCORE E PRIORITY
# ─────────────────────────────────────────────

def calcular_score(lead: dict) -> int:
    score = 50
    try:
        rating = float(lead.get("rating") or 0)
        if rating >= 4.5:        score += 20
        elif rating >= 4.0:      score += 15
        elif rating >= 3.5:      score += 8
        elif 0 < rating < 3.0:   score -= 10
    except (ValueError, TypeError):
        pass

    if lead.get("tem_site") == "false":  score += 20
    elif lead.get("tem_site") == "true": score -= 5

    ng = lead.get("nicho_group", "")
    if ng in ["food", "beleza"]:           score += 10
    elif ng in ["saude", "varejo"]:        score += 5
    elif ng in ["automotivo", "servicos"]: score += 3

    return max(0, min(100, score))


def calcular_priority(score: int) -> str:
    if score >= 75: return "quente"
    if score >= 50: return "morno"
    return "baixo"

# ─────────────────────────────────────────────
#  EVOLUTION API
# ─────────────────────────────────────────────

def verificar_whatsapp(numeros: list[str]) -> dict[str, bool]:
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
            if resp.ok:
                break
            if 400 <= resp.status_code < 500:
                log.error(f"Evolution API erro {resp.status_code}: {resp.text[:300]}")
                return {}
            log.warning(f"Evolution API {resp.status_code} (tentativa {tentativa+1}/3)")
            if tentativa < len(esperas) - 1:
                time.sleep(espera)
            else:
                return {}
        except requests.exceptions.RequestException as e:
            log.error(f"Evolution API timeout/erro (tentativa {tentativa+1}/3): {e}")
            if tentativa < len(esperas) - 1:
                time.sleep(espera)
            else:
                return {}

    if resp is None:
        return {}

    try:
        data = resp.json()
    except Exception:
        log.error(f"Evolution API JSON inválido. Resposta bruta: {resp.text[:200]}")
        return {}

    lista = data if isinstance(data, list) else []
    resultado = {}
    for item in lista:
        if not isinstance(item, dict):
            continue
        raw_num = str(item.get("number") or item.get("jid") or "")
        num = re.sub(r"[^0-9]", "", raw_num)
        if num:
            resultado[num] = item.get("exists") is True

    com_wpp = sum(1 for v in resultado.values() if v)
    log.info(f"Evolution API: {com_wpp} com WPP de {len(numeros)} verificados.")
    return resultado

# ─────────────────────────────────────────────
#  PRÉ-ANÁLISE DA ABA  [NOVO v5]
# ─────────────────────────────────────────────

def pre_analise_aba(
    registros: list[dict],
    nome_aba: str,
    numeros_existentes: set,
    numeros_processados: set,
) -> dict:
    """
    [NOVO v5]
    Analisa quantos leads da aba são novos vs já validados vs já verificados (sem WPP).
    Não consome API — apenas classifica para dar visibilidade antes de processar.
    """
    total       = len(registros)
    sem_numero  = 0
    ja_validado = 0
    ja_verificado_sem_wpp = 0
    novos       = 0

    for row in registros:
        numero = normalizar_numero(row.get("phoneNumber") or row.get("phone") or "")
        if not numero:
            sem_numero += 1
            continue
        digits = re.sub(r"\D", "", numero)
        if digits in numeros_existentes:
            ja_validado += 1
        elif digits in numeros_processados:
            ja_verificado_sem_wpp += 1
        else:
            novos += 1

    log.info(
        f"  📊 Pré-análise '{nome_aba}': "
        f"Total={total} | Novos={novos} | "
        f"Já com WPP={ja_validado} | "
        f"Já verificados (sem WPP)={ja_verificado_sem_wpp} | "
        f"Sem número válido={sem_numero}"
    )
    return {"total": total, "novos": novos, "ja_validado": ja_validado,
            "ja_verificado_sem_wpp": ja_verificado_sem_wpp, "sem_numero": sem_numero}

# ─────────────────────────────────────────────
#  PREPARAR LOTE  [BUG#2 CORRIGIDO]
# ─────────────────────────────────────────────

def preparar_lote(
    registros: list[dict],
    cursor: int,
    nome_aba: str,
    numeros_existentes: set,
    numeros_processados: set,
    vistos_sessao: set,
    debug_numeros: bool = False,
) -> tuple[list[dict], int, dict]:
    """
    [BUG#2 CORRIGIDO]
    Agora retorna também estatísticas de skip com motivo, e loga amostra de números
    no 1º lote para debug de formatação.
    """
    lote = []
    i = cursor
    stats = {"sem_numero": 0, "ja_validado": 0, "ja_processado": 0, "sem_nome": 0}
    amostras_raw = []

    while i < len(registros) and len(lote) < LOTE_SIZE:
        row = registros[i]
        i += 1

        raw_phone = row.get("phoneNumber") or row.get("phone") or ""
        numero = normalizar_numero(raw_phone)

        # [NOVO v5 DEBUG] Coleta amostra dos primeiros números brutos
        if debug_numeros and len(amostras_raw) < 5:
            amostras_raw.append(f"'{raw_phone}' → '{numero}'")

        if not numero:
            stats["sem_numero"] += 1
            continue

        digits = re.sub(r"\D", "", numero)

        # [BUG#2 FIX] Dedup com motivo detalhado
        if digits in numeros_existentes:
            stats["ja_validado"] += 1
            continue
        if digits in numeros_processados or digits in vistos_sessao:
            stats["ja_processado"] += 1
            continue

        nome = str(row.get("title") or row.get("nome") or "").strip()
        if not nome:
            stats["sem_nome"] += 1
            continue

        nicho   = str(row.get("type") or row.get("nicho") or "geral").lower().strip()
        website = str(row.get("website") or "").strip()
        ng      = nicho_group(nicho)
        ts      = "true" if len(website) > 3 else "false"

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
        lead["whatsapp_confirmado"] = "pendente"   # será atualizado após verificação
        lead["data_verificacao"]    = datetime.now().strftime("%d/%m/%Y")

        vistos_sessao.add(digits)
        lote.append(lead)

    # Log amostra para debug
    if debug_numeros and amostras_raw:
        log.info(f"  🔍 Amostra de números (raw → normalizado): {' | '.join(amostras_raw)}")

    return lote, i, stats

# ─────────────────────────────────────────────
#  RELATÓRIO FINAL
# ─────────────────────────────────────────────

def salvar_relatorio(stats: dict):
    with open(RELATORIO_FILE, "w", encoding="utf-8") as f:
        json.dump(stats, f, ensure_ascii=False, indent=2)
    log.info(f"Relatório salvo em {RELATORIO_FILE}")

# ─────────────────────────────────────────────
#  LOOP PRINCIPAL
# ─────────────────────────────────────────────


def menu_inicial():
    """
    Gerencia estado antes de iniciar.
    Para resetar: mude FORCAR_RESET = True no topo do arquivo,
    suba para o GitHub, deixe o EasyPanel redeploy, depois volte para False.
    """
    tem_estado      = Path(ESTADO_FILE).exists()
    tem_processados = Path(PROCESSADOS_FILE).exists()

    if FORCAR_RESET:
        log.info("⚠️  FORCAR_RESET=True — apagando estado e processados...")
        if tem_estado:
            Path(ESTADO_FILE).unlink()
            log.info("🗑️  estado.json apagado.")
        if tem_processados:
            Path(PROCESSADOS_FILE).unlink()
            log.info("🗑️  processados.json apagado.")
        log.info("✅ Reset concluído. Lembre de voltar FORCAR_RESET=False no código!")
        return

    if not tem_estado and not tem_processados:
        log.info("Nenhum progresso salvo. Iniciando do zero.")
        return

    if tem_estado:
        with open(ESTADO_FILE) as f:
            e = json.load(f)
        aba_atual = ABAS[min(e.get("aba_index", 0), len(ABAS)-1)]
        log.info(f"Progresso encontrado → aba: {aba_atual} | gravados: {e.get('total_gravados', 0)}")
    if tem_processados:
        with open(PROCESSADOS_FILE) as f:
            p = json.load(f)
        log.info(f"Processados encontrados → {len(p.get('numeros', []))} números já verificados")
    log.info("Continuando de onde parou. (Para resetar: FORCAR_RESET=True no topo do arquivo)")

def main():
    global _shutdown

    menu_inicial()  # ← pergunta antes de qualquer coisa

    log.info("=" * 60)
    log.info("VisívelAgora — Qualificador de Leads v5")
    log.info(f"Início: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    log.info(f"Abas: {len(ABAS)} | Lotes de {LOTE_SIZE} | Delay {DELAY_ENTRE_LOTES}-{DELAY_ENTRE_LOTES+DELAY_JITTER}s | DRY_RUN={DRY_RUN}")
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
        log.info("✅ Todas as abas já foram processadas.")
        return

    # Carrega validados (com WPP) e processados (sem WPP) para dedup completa
    log.info("Carregando dados para deduplicação...")
    numeros_existentes  = ler_numeros_validados(planilha)   # com WPP → não reprocessar
    numeros_processados = carregar_processados()             # [NOVO v5] sem WPP → não re-verificar
    vistos_sessao = set()

    # Estatísticas da sessão
    data_inicio       = datetime.now()
    total_lidos       = 0
    total_com_wpp     = 0
    total_sem_wpp     = 0
    gravados_sessao   = 0
    tempos_lote       = []
    por_nicho         = {k: 0 for k in ["food","beleza","saude","varejo","automotivo","educacao","servicos","entretenimento"]}
    stats_por_aba     = {}
    num_lote_global   = 0
    novos_processados = set()   # [NOVO v5] acumula para salvar no finally

    try:
        while aba_index < len(ABAS) and not _shutdown:
            nome_aba = ABAS[aba_index]
            log.info(f"\n{'─'*50}")
            log.info(f"📂 Aba {aba_index+1}/{len(ABAS)}: {nome_aba} (cursor={cursor})")

            registros = ler_aba(planilha, nome_aba)

            if not registros:
                log.warning(f"Aba '{nome_aba}' vazia ou inexistente. Pulando.")
                aba_index += 1
                cursor = 0
                salvar_estado({"aba_index": aba_index, "cursor": cursor, "total_gravados": total_gravados})
                continue

            # [NOVO v5] Pré-análise para visibilidade
            analise = pre_analise_aba(registros, nome_aba, numeros_existentes, numeros_processados)
            stats_por_aba[nome_aba] = analise

            if analise["novos"] == 0:
                log.info(f"  ⏩ Aba '{nome_aba}' sem leads novos. Pulando sem delay.")
                aba_index += 1
                cursor = 0
                salvar_estado({"aba_index": aba_index, "cursor": cursor, "total_gravados": total_gravados})
                continue

            # [BUG#3 FIX] Estimativa baseada em leads novos, não no total do arquivo
            leads_novos_estimados = analise["novos"]
            lotes_estimados = max(1, (leads_novos_estimados // LOTE_SIZE) + 1)
            log.info(f"  ⏱️  Estimativa: ~{lotes_estimados} lotes reais ({leads_novos_estimados} leads novos)")

            num_lote_aba  = 0
            wpp_aba       = 0
            sem_wpp_aba   = 0
            debug_primeira_vez = True   # amostra de números só no 1º lote

            while cursor < len(registros) and not _shutdown:
                t_inicio = time.time()
                num_lote_global += 1
                num_lote_aba    += 1

                lote, novo_cursor, skip_stats = preparar_lote(
                    registros, cursor, nome_aba,
                    numeros_existentes, numeros_processados, vistos_sessao,
                    debug_numeros=debug_primeira_vez,
                )
                debug_primeira_vez = False
                cursor = novo_cursor
                total_lidos += len(lote)

                # [BUG#2 FIX] Log de skips detalhado
                total_skips = sum(skip_stats.values())
                if total_skips > 0:
                    log.info(
                        f"  ↩️  Pulados nesse trecho: {total_skips} "
                        f"(já com WPP={skip_stats['ja_validado']} | "
                        f"já verificado={skip_stats['ja_processado']} | "
                        f"sem número={skip_stats['sem_numero']} | "
                        f"sem nome={skip_stats['sem_nome']})"
                    )

                if not lote:
                    # Sem leads válidos nesse trecho — avança sem delay
                    continue

                # Verifica WhatsApp
                wpp_map = verificar_whatsapp([l["numero"] for l in lote])

                com_wpp     = []
                sem_wpp     = []

                for lead in lote:
                    digits = re.sub(r"\D", "", lead["numero"])
                    if wpp_map.get(digits) is True:
                        lead["whatsapp_confirmado"] = "true"
                        numeros_existentes.add(digits)
                        com_wpp.append(lead)
                        por_nicho[lead["nicho_group"]] = por_nicho.get(lead["nicho_group"], 0) + 1
                    else:
                        lead["whatsapp_confirmado"] = "false"
                        sem_wpp.append(lead)

                    # [NOVO v5 BUG#1 FIX] Marca como processado independente do resultado WPP
                    numeros_processados.add(digits)
                    novos_processados.add(digits)

                total_com_wpp   += len(com_wpp)
                total_sem_wpp   += len(sem_wpp)
                wpp_aba         += len(com_wpp)
                sem_wpp_aba     += len(sem_wpp)
                gravados_sessao += len(com_wpp)

                if com_wpp:
                    gravar_leads_validados(planilha, com_wpp)
                    total_gravados += len(com_wpp)

                if sem_wpp:
                    gravar_leads_sem_wpp(planilha, sem_wpp)

                # Salva processados a cada lote (segurança)
                if novos_processados:
                    numeros_processados.update(novos_processados)
                    salvar_processados(numeros_processados)
                    novos_processados.clear()

                # Salva progresso
                salvar_estado({"aba_index": aba_index, "cursor": cursor, "total_gravados": total_gravados})

                # [BUG#3 FIX] Estimativa de tempo com base em lotes reais
                t_lote = time.time() - t_inicio
                tempos_lote.append(t_lote)
                media_t = sum(tempos_lote[-20:]) / len(tempos_lote[-20:])  # média dos últimos 20
                lotes_restantes_aba = max(0, lotes_estimados - num_lote_aba)
                est_min = int((lotes_restantes_aba * media_t) / 60)

                # % de conclusão da aba baseado no cursor
                pct_aba = int((cursor / len(registros)) * 100)

                taxa_wpp_lote = f"{round(len(com_wpp)/len(lote)*100)}%" if lote else "0%"

                log.info(
                    f"[{nome_aba}] Lote {num_lote_aba} | "
                    f"WPP: {len(com_wpp)}/{len(lote)} ({taxa_wpp_lote}) | "
                    f"Sessão: {gravados_sessao} | "
                    f"Total geral: {total_gravados} | "
                    f"Aba: {pct_aba}% | "
                    f"~{est_min}min restantes"
                )

                delay = DELAY_ENTRE_LOTES + random.randint(0, DELAY_JITTER)
                log.info(f"Aguardando {delay}s...")
                time.sleep(delay)

            if not _shutdown:
                log.info(
                    f"✅ Aba '{nome_aba}' concluída — "
                    f"WPP encontrados: {wpp_aba} | Sem WPP: {sem_wpp_aba}"
                )
                aba_index += 1
                cursor = 0
                salvar_estado({"aba_index": aba_index, "cursor": cursor, "total_gravados": total_gravados})

    finally:
        # Salva processados finais
        if novos_processados:
            numeros_processados.update(novos_processados)
            salvar_processados(numeros_processados)

        data_fim = datetime.now()
        duracao  = int((data_fim - data_inicio).total_seconds() / 60)
        taxa_wpp = f"{round(total_com_wpp / total_lidos * 100, 1)}%" if total_lidos > 0 else "0%"

        relatorio = {
            "versao":          "v5",
            "data_inicio":     data_inicio.strftime("%d/%m/%Y %H:%M:%S"),
            "data_fim":        data_fim.strftime("%d/%m/%Y %H:%M:%S"),
            "duracao_minutos": duracao,
            "total_lidos":     total_lidos,
            "total_com_wpp":   total_com_wpp,
            "total_sem_wpp":   total_sem_wpp,
            "total_gravados":  total_gravados,
            "taxa_wpp":        taxa_wpp,
            "por_nicho_group": por_nicho,
            "por_aba":         stats_por_aba,
        }
        salvar_relatorio(relatorio)

        if _shutdown:
            log.info("Script encerrado pelo usuário. Estado salvo — rode novamente para retomar.")
        else:
            log.info("\n" + "=" * 60)
            log.info("🎉 PROCESSAMENTO CONCLUÍDO!")
            log.info(f"Total gravados: {total_gravados} | Taxa WPP: {taxa_wpp}")
            log.info(f"Total verificados sem WPP: {total_sem_wpp}")
            log.info("=" * 60)


if __name__ == "__main__":
    main()
