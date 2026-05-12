"""
VisívelAgora — Qualificador de Leads v9
========================================
SEM verificação de WhatsApp — zero risco de ban.

A verificação de WPP será feita pelo Motor Principal
antes de cada envio, um número por um.

O que esse script faz:
  1. Lê as 15 abas + leads_raw (auditoria)
  2. Filtra números inválidos
  3. Deduplica contra leads_validados
  4. Calcula score e priority_level
  5. Grava direto em leads_validados

Velocidade: ~500 leads/minuto (limitado pelo Sheets)
Tempo estimado: 1-2 horas para todos os 16k leads
"""

import json
import re
import time
import signal
import logging
import random
from pathlib import Path
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

# ═══════════════════════════════════════════════════════
#  CONFIGURAÇÕES
# ═══════════════════════════════════════════════════════

SPREADSHEET_ID    = "1yiZQnUqumPgVRv9DbF3aQzgwbJt6L3RlaucvesXKxSc"
GOOGLE_CREDS_FILE = "credentials.json"

DATA_DIR = Path("/app/data")
DATA_DIR.mkdir(parents=True, exist_ok=True)

ESTADO_FILE    = DATA_DIR / "estado.json"
RELATORIO_FILE = DATA_DIR / "relatorio_final.json"
LOG_FILE       = Path("/app/qualificar_leads.log")

LOTE_SIZE  = 25   # sem API externa, pode usar lotes maiores
DELAY_LOTE = 3    # segundos — só para respeitar rate limit do Sheets

DRY_RUN      = False
FORCAR_RESET = False

ABAS = [
    "leads_raw_1k",  "leads_raw_2k",  "leads_raw_3k",
    "leads_raw_4k",  "leads_raw_5k",  "leads_raw_6k",
    "leads_raw_7k",  "leads_raw_8k",  "leads_raw_9k",
    "leads_raw_10k", "leads_raw_11k", "leads_raw_12k",
    "leads_raw_13k", "leads_raw_14k", "leads_raw_15k",
    "leads_raw",  # auditoria final
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
#  ATOMIC WRITE
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
    if FORCAR_RESET or not ESTADO_FILE.exists():
        if FORCAR_RESET:
            log.warning("FORCAR_RESET=True — reiniciando do zero.")
        return {"aba_index": 0, "cursor": 0, "total_gravados": 0}
    try:
        with open(ESTADO_FILE, "r", encoding="utf-8") as f:
            estado = json.load(f)
        aba_nome = ABAS[min(estado["aba_index"], len(ABAS) - 1)]
        log.info(f"Retomando: aba={aba_nome} | cursor={estado['cursor']} | gravados={estado['total_gravados']}")
        return estado
    except Exception as e:
        log.error(f"Erro ao carregar estado: {e}. Reiniciando do zero.")
        return {"aba_index": 0, "cursor": 0, "total_gravados": 0}

def salvar_estado(estado: dict):
    atomic_write_json(ESTADO_FILE, estado)

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
    log.info(f"✅ Conectado: {planilha.title}")
    return planilha


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
        log.info(f"leads_validados: {len(numeros)} números já registrados.")
        return numeros
    except Exception as e:
        log.warning(f"Erro ao ler leads_validados: {e}. Assumindo vazio.")
        return set()


def gravar_linhas(planilha: gspread.Spreadsheet, nome_aba: str, linhas: list):
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
            log.error(f"Erro ao gravar (tentativa {tentativa+1}/3): {e}")
            if tentativa < len(esperas) - 1:
                time.sleep(espera)
            else:
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
    sufixo = p[2:]
    if len(p) == 10 and sufixo[0] in ["2", "3", "4", "5"]:
        return None  # fixo comercial
    if len(p) == 11 and sufixo[0] != "9":
        return None  # não é celular
    return "55" + p

# ═══════════════════════════════════════════════════════
#  NICHO GROUP
# ═══════════════════════════════════════════════════════

def nicho_group(nicho: str) -> str:
    n = (nicho or "").lower()
    if any(k in n for k in ["pizzaria","restaurante","lanche","hamburgue","hamburgueria","esfiaria","churrascaria","japonesa","árabe","arabe","food truck","cafeteria","padaria","confeitaria","açaí","acai","sorvete","sorveteria","salgado","doceria","lanchonete","delivery","marmita","cozinha","bistrô","bistro","quilo","buffet","cantina"]):
        return "food"
    if any(k in n for k in ["barbearia","beleza","estetica","estética","tatuagem","micropigmentacao","depilacao","depilação","spa","salão","salao","manicure","cabeleireiro","studio","nail","sobrancelha","cabelo","esteticista","bronzeamento","podologia"]):
        return "beleza"
    if any(k in n for k in ["odontologia","dentista","clinica","clínica","psicólogo","psicologo","fisioterapia","nutricionista","farmacia","farmácia","academia","médico","medico","hospital","laboratório","laboratorio","veterinário","veterinario","pet","terapia","terapeuta","reabilitação","laser"]):
        return "saude"
    if any(k in n for k in ["concessionaria","concessionária","mecanica","mecânica","borracharia","lava jato","funilaria","guincho","oficina","pneus","auto","autopeças","autopecas","insulfilm"]):
        return "automotivo"
    if any(k in n for k in ["escola","curso","faculdade","creche","idiomas","reforço","reforco","colégio","colegio","ensino","inglês","música","dança"]):
        return "educacao"
    if any(k in n for k in ["serralheria","eletricista","encanador","dedetizadora","imobiliaria","imobiliária","advocacia","contabilidade","limpeza","reformas","chaveiro","fotografia","cartório","segurança","ar condicionado"]):
        return "servicos"
    if any(k in n for k in ["bar","boate","clube","shows","evento","festa","karaoke","casa de festas","brinquedoteca"]):
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
        elif rating == 0.0:      score -= 5
        elif rating < 3.0:       score -= 15
    except (ValueError, TypeError):
        score -= 5
    if lead.get("tem_site") == "false":   score += 20
    elif lead.get("tem_site") == "true":  score -= 5
    ng = lead.get("nicho_group", "varejo")
    if ng in ["food", "beleza"]:             score += 15
    elif ng == "saude":                      score += 10
    elif ng == "varejo":                     score += 5
    elif ng in ["automotivo", "servicos"]:   score += 3
    return max(0, min(100, score))


def calcular_priority(score: int) -> str:
    if score >= 75: return "quente"
    if score >= 50: return "morno"
    return "baixo"

# ═══════════════════════════════════════════════════════
#  RELATÓRIO
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
    log.info("VisívelAgora — Qualificador de Leads v9 (sem WPP check)")
    log.info(f"Início: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    log.info(f"Abas: {len(ABAS)} | Lotes de {LOTE_SIZE} | Delay {DELAY_LOTE}s | DRY_RUN={DRY_RUN}")
    log.info("=" * 60)

    if not Path(GOOGLE_CREDS_FILE).exists():
        log.error(f"'{GOOGLE_CREDS_FILE}' não encontrado!")
        return

    planilha = conectar_sheets()

    estado         = carregar_estado()
    aba_index      = estado["aba_index"]
    cursor         = estado["cursor"]
    total_gravados = estado["total_gravados"]

    if aba_index >= len(ABAS):
        log.info("✅ Todas as abas já foram processadas.")
        return

    log.info("Carregando números já validados para deduplicação...")
    numeros_existentes = ler_numeros_validados(planilha)
    vistos_sessao      = set()

    data_inicio     = datetime.now()
    gravados_sessao = 0
    por_nicho       = {k: 0 for k in ["food","beleza","saude","varejo","automotivo","educacao","servicos","entretenimento"]}

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

            gravados_aba  = 0
            pulados_aba   = 0
            num_lote      = 0

            while cursor < len(registros) and not _shutdown:
                num_lote    += 1
                lote         = []
                novo_cursor  = cursor
                pulados_lote = {"ja_validado": 0, "sem_numero": 0, "sem_nome": 0}

                # Monta lote
                while novo_cursor < len(registros) and len(lote) < LOTE_SIZE:
                    row         = registros[novo_cursor]
                    novo_cursor += 1

                    numero = normalizar_numero(row.get("phoneNumber") or row.get("phone") or "")
                    if not numero:
                        pulados_lote["sem_numero"] += 1
                        continue

                    digits = re.sub(r"\D", "", numero)

                    if digits in numeros_existentes or digits in vistos_sessao:
                        pulados_lote["ja_validado"] += 1
                        continue

                    nome = str(row.get("title") or row.get("nome") or "").strip()
                    if not nome:
                        pulados_lote["sem_nome"] += 1
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
                    lead["whatsapp_confirmado"] = "pendente"
                    lead["data_verificacao"]    = datetime.now().strftime("%d/%m/%Y")

                    vistos_sessao.add(digits)
                    lote.append(lead)

                total_pulados = sum(pulados_lote.values())
                if total_pulados > 0:
                    log.info(f"  ↩️  Pulados: {total_pulados} (já validado={pulados_lote['ja_validado']} | sem número={pulados_lote['sem_numero']} | sem nome={pulados_lote['sem_nome']})")

                if not lote:
                    cursor = novo_cursor
                    salvar_estado({"aba_index": aba_index, "cursor": cursor, "total_gravados": total_gravados})
                    continue

                # Grava lote — cursor só avança após sucesso
                linhas = [
                    [
                        l["numero"], l["nome"], l["nicho"], l["nicho_group"],
                        l["endereco"], l["rating"], l["tem_site"], l["origem_aba"],
                        l["score"], l["priority_level"], l["whatsapp_confirmado"], l["data_verificacao"],
                    ]
                    for l in lote
                ]

                try:
                    gravar_linhas(planilha, "leads_validados", linhas)

                    for lead in lote:
                        digits = re.sub(r"\D", "", lead["numero"])
                        numeros_existentes.add(digits)
                        por_nicho[lead["nicho_group"]] = por_nicho.get(lead["nicho_group"], 0) + 1

                    gravados_aba    += len(lote)
                    gravados_sessao += len(lote)
                    total_gravados  += len(lote)
                    cursor           = novo_cursor

                    salvar_estado({"aba_index": aba_index, "cursor": cursor, "total_gravados": total_gravados})

                except Exception as e:
                    log.error(f"Falha ao gravar. Cursor NÃO avançado. Erro: {e}")
                    for lead in lote:
                        vistos_sessao.discard(re.sub(r"\D", "", lead["numero"]))
                    time.sleep(15)
                    continue

                pct = round(cursor / len(registros) * 100)
                log.info(
                    f"[{nome_aba}] Lote {num_lote} | "
                    f"Gravados: {len(lote)} | "
                    f"Sessão: {gravados_sessao} | "
                    f"Total: {total_gravados} | "
                    f"Aba: {pct}%"
                )

                time.sleep(DELAY_LOTE)

            if not _shutdown:
                log.info(f"✅ Aba '{nome_aba}' concluída — {gravados_aba} leads gravados.")
                aba_index += 1
                cursor = 0
                salvar_estado({"aba_index": aba_index, "cursor": cursor, "total_gravados": total_gravados})

    finally:
        data_fim = datetime.now()
        duracao  = int((data_fim - data_inicio).total_seconds() / 60)

        relatorio = {
            "data_inicio":     data_inicio.strftime("%d/%m/%Y %H:%M:%S"),
            "data_fim":        data_fim.strftime("%d/%m/%Y %H:%M:%S"),
            "duracao_minutos": duracao,
            "total_gravados":  total_gravados,
            "por_nicho_group": por_nicho,
            "nota":            "WPP não verificado — pendente no Motor Principal",
        }
        salvar_relatorio(relatorio)

        if _shutdown:
            log.info("Script encerrado. Estado salvo — rode novamente para retomar.")
        else:
            log.info("\n" + "=" * 60)
            log.info("🎉 CONCLUÍDO!")
            log.info(f"Total gravados em leads_validados: {total_gravados}")
            log.info("=" * 60)


if __name__ == "__main__":
    main()
