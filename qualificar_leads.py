"""
VisívelAgora — Qualificador de Leads v3
========================================
Lê leads_raw (16k), valida WhatsApp via Evolution API,
deduplica e grava em leads_validados com score e priority_level.
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
RELATORIO_FILE    = "relatorio_final.json"

LOTE_SIZE         = 10
DELAY_ENTRE_LOTES = 30
DELAY_JITTER      = 10
HORA_INICIO       = 8
HORA_FIM          = 20

DRY_RUN = False  # True = não grava nada, não chama Evolution API

ABAS = ["leads_raw"]

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
    log.info("Sinal de encerramento recebido. Finalizando após o lote atual...")
    _shutdown = True

signal.signal(signal.SIGINT,  _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)

# ─────────────────────────────────────────────
#  ESTADO
# ─────────────────────────────────────────────

def carregar_estado() -> dict:
    if Path(ESTADO_FILE).exists():
        with open(ESTADO_FILE, "r", encoding="utf-8") as f:
            estado = json.load(f)
        log.info(f"Retomando: aba={ABAS[min(estado['aba_index'], len(ABAS)-1)]}, cursor={estado['cursor']}, gravados={estado['total_gravados']}")
        return estado
    log.info("Nenhum estado salvo. Começando do zero.")
    return {"aba_index": 0, "cursor": 0, "total_gravados": 0}


def salvar_estado(estado: dict):
    with open(ESTADO_FILE, "w", encoding="utf-8") as f:
        json.dump(estado, f, ensure_ascii=False, indent=2)

# ─────────────────────────────────────────────
#  CONTROLE DE HORÁRIO
# ─────────────────────────────────────────────

def dentro_do_horario() -> bool:
    hora = datetime.now().hour
    return HORA_INICIO <= hora < HORA_FIM


def aguardar_horario():
    while not dentro_do_horario() and not _shutdown:
        agora = datetime.now()
        log.info(f"Fora do horário ({HORA_INICIO}h-{HORA_FIM}h). Aguardando... [{agora.strftime('%H:%M')}]")
        time.sleep(600)

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
        log.warning(f"Aba '{nome_aba}' não encontrada.")
        return []
    except Exception as e:
        log.error(f"Erro ao ler aba '{nome_aba}': {e}")
        return []


def ler_numeros_validados(planilha: gspread.Spreadsheet) -> set:
    try:
        aba = planilha.worksheet("leads_validados")
        valores = aba.col_values(1)
        numeros = set(
            re.sub(r"\D", "", v)
            for v in valores[1:]
            if v and re.sub(r"\D", "", v)
        )
        log.info(f"leads_validados: {len(numeros)} números já registrados.")
        return numeros
    except Exception as e:
        log.warning(f"Erro ao ler leads_validados: {e}. Assumindo vazio.")
        return set()


def gravar_leads_validados(planilha: gspread.Spreadsheet, leads: list[dict]):
    if not leads or DRY_RUN:
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

    tentativas = [5, 15, 30]
    for i, espera in enumerate(tentativas):
        try:
            aba = planilha.worksheet("leads_validados")
            aba.append_rows(linhas, value_input_option="RAW")
            log.info(f"✅ Gravados {len(linhas)} leads em leads_validados.")
            return
        except Exception as e:
            log.error(f"Erro ao gravar (tentativa {i+1}/3): {e}")
            if i < len(tentativas) - 1:
                log.info(f"Aguardando {espera}s para retry...")
                time.sleep(espera)
            else:
                log.error("Falha definitiva ao gravar. Lote perdido.")
                raise

# ─────────────────────────────────────────────
#  VALIDAÇÃO DE CREDENCIAIS
# ─────────────────────────────────────────────

def validar_credenciais(planilha: gspread.Spreadsheet) -> bool:
    log.info("Validando credenciais e conexões...")

    # Testa leitura do Sheets
    try:
        aba = planilha.worksheet("leads_raw")
        aba.row_values(1)
        log.info("✅ Google Sheets: OK")
    except Exception as e:
        log.error(f"❌ Google Sheets falhou: {e}")
        return False

    # Testa Evolution API com array vazio
    if not DRY_RUN:
        try:
            url = f"{EVOLUTION_URL}/chat/whatsappNumbers/{requests.utils.quote(EVOLUTION_INST)}"
            headers = {"apikey": EVOLUTION_APIKEY, "Content-Type": "application/json"}
            resp = requests.post(url, headers=headers, json={"numbers": []}, timeout=10)
            if resp.status_code in [200, 400]:
                log.info("✅ Evolution API: OK")
            else:
                log.error(f"❌ Evolution API retornou {resp.status_code}: {resp.text[:200]}")
                return False
        except Exception as e:
            log.error(f"❌ Evolution API falhou: {e}")
            return False
    else:
        log.info("[DRY_RUN] Pulando validação da Evolution API.")

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
    sufixo = p[2:]
    if len(p) == 10 and sufixo[0] in ["2", "3", "4", "5"]:
        return None  # fixo comercial — sem WhatsApp
    if len(p) == 11 and sufixo[0] != "9":
        return None
    return "55" + p

# ─────────────────────────────────────────────
#  NICHO GROUP
# Ordem importa: saude antes de entretenimento (laser),
#                beleza antes de servicos (limpeza de pele)
# ─────────────────────────────────────────────

def nicho_group(nicho: str) -> str:
    n = (nicho or "").lower()

    if any(k in n for k in [
        "pizzaria","restaurante","lanche","hamburgue","hamburgueria","esfiaria",
        "churrascaria","japonesa","árabe","arabe","food truck","cafeteria","padaria",
        "confeitaria","açaí","acai","sorvete","sorveteria","salgado","doceria",
        "lanchonete","delivery","marmita","cozinha","bistrô","bistro","quilo",
        "buffet","cantina"
    ]):
        return "food"

    if any(k in n for k in [
        "barbearia","beleza","estetica","estética","tatuagem","micropigmentacao",
        "depilacao","depilação","spa","salão","salao","manicure","cabeleireiro",
        "salao de beleza","studio","nail","design de sobrancelha","sobrancelha",
        "cabelo","esteticista","bronzeamento","podologia","podólogo","podólogo"
    ]):
        return "beleza"

    if any(k in n for k in [
        "odontologia","dentista","clinica","clínica","psicólogo","psicologo",
        "fisioterapia","nutricionista","farmacia","farmácia","academia",
        "médico","medico","hospital","laboratório","laboratorio","exame",
        "cirurgia","ortopedia","pediatria","cardiologia","dermatologia",
        "psiquiatria","terapia","terapeuta","veterinário","veterinario",
        "pet","acupuntura","homeopatia","reabilitação","laser"
    ]):
        return "saude"

    if any(k in n for k in [
        "concessionaria","concessionária","mecanica","mecânica","borracharia",
        "lava jato","funilaria","guincho","oficina","pneus","auto","autopeças",
        "autopecas","troca de óleo","elétrica automotiva","insulfilm",
        "som automotivo","blindagem","despachante"
    ]):
        return "automotivo"

    if any(k in n for k in [
        "escola","curso","faculdade","creche","idiomas","reforço","reforco",
        "colégio","colegio","ensino","pré-escola","pre escola","berçário",
        "bercario","inglês","ingles","música","musica","dança","danca",
        "arte","reforço escolar"
    ]):
        return "educacao"

    if any(k in n for k in [
        "serralheria","eletricista","encanador","dedetizadora","imobiliaria",
        "imobiliária","advocacia","contabilidade","segurança","seguranca",
        "vigilância","limpeza","zeladoria","reformas","pintura",
        "marido de aluguel","desentupimento","ar condicionado","refrigeração",
        "chaveiro","vidraçaria","vidracaria","gráfica","grafica","fotografia",
        "fotografo","cartório","cartorio"
    ]):
        return "servicos"

    if any(k in n for k in [
        "bar","boate","clube","shows","evento","festa","buffet infantil",
        "salão de festas","karaoke","karaokê","espaço para eventos",
        "casa de festas","brinquedoteca"
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
        if rating >= 4.5:   score += 20
        elif rating >= 4.0: score += 15
        elif rating >= 3.5: score += 8
        elif 0 < rating < 3.0: score -= 10
    except (ValueError, TypeError):
        pass

    if lead.get("tem_site") == "false":
        score += 20
    elif lead.get("tem_site") == "true":
        score -= 5

    ng = lead.get("nicho_group", "")
    if ng in ["food", "beleza"]:       score += 10
    elif ng in ["saude", "varejo"]:    score += 5
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
        log.info(f"[DRY_RUN] Simulando verificação de {len(numeros)} números.")
        return {re.sub(r"[^0-9]", "", n): True for n in numeros}

    numeros_puros = [re.sub(r"[^0-9]", "", n) for n in numeros]
    payload = {"numbers": numeros_puros}
    url = f"{EVOLUTION_URL}/chat/whatsappNumbers/{requests.utils.quote(EVOLUTION_INST)}"
    headers = {"apikey": EVOLUTION_APIKEY, "Content-Type": "application/json"}

    tentativas = [5, 15, 30]
    for i, espera in enumerate(tentativas):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            if resp.ok:
                break
            # 4xx não adianta retry
            if 400 <= resp.status_code < 500:
                log.error(f"Evolution API erro {resp.status_code}: {resp.text[:300]}")
                return {}
            log.error(f"Evolution API erro {resp.status_code} (tentativa {i+1}/3): {resp.text[:200]}")
            if i < len(tentativas) - 1:
                log.info(f"Aguardando {espera}s para retry...")
                time.sleep(espera)
            else:
                return {}
        except requests.exceptions.RequestException as e:
            log.error(f"Erro na Evolution API (tentativa {i+1}/3): {e}")
            if i < len(tentativas) - 1:
                time.sleep(espera)
            else:
                return {}

    try:
        data = resp.json()
    except Exception:
        log.error("Evolution API retornou resposta inválida.")
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
#  PREPARAR LOTE
# ─────────────────────────────────────────────

def preparar_lote(
    registros: list[dict],
    cursor: int,
    nome_aba: str,
    numeros_existentes: set,
    vistos_sessao: set,
) -> tuple[list[dict], int]:
    lote = []
    i = cursor

    while i < len(registros) and len(lote) < LOTE_SIZE:
        row = registros[i]
        i += 1

        numero = normalizar_numero(row.get("phoneNumber") or row.get("phone") or "")
        if not numero:
            continue

        digits = re.sub(r"\D", "", numero)
        if digits in numeros_existentes or digits in vistos_sessao:
            continue

        nome = str(row.get("title") or row.get("nome") or "").strip()
        if not nome:
            continue

        nicho  = str(row.get("type") or row.get("nicho") or "geral").lower().strip()
        website = str(row.get("website") or "").strip()
        ng     = nicho_group(nicho)
        ts     = "true" if len(website) > 3 else "false"

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

    return lote, i

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

def main():
    global _shutdown

    log.info("=" * 60)
    log.info("VisívelAgora — Qualificador de Leads v3")
    log.info(f"Horário: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}")
    log.info(f"Lotes de {LOTE_SIZE} | Delay {DELAY_ENTRE_LOTES}-{DELAY_ENTRE_LOTES+DELAY_JITTER}s | DRY_RUN={DRY_RUN}")
    log.info("=" * 60)

    if not Path(GOOGLE_CREDS_FILE).exists():
        log.error(f"Arquivo '{GOOGLE_CREDS_FILE}' não encontrado!")
        return

    try:
        with open(GOOGLE_CREDS_FILE) as f:
            json.load(f)
    except Exception as e:
        log.error(f"credentials.json inválido: {e}")
        return

    planilha = conectar_sheets()

    if not validar_credenciais(planilha):
        log.error("Validação falhou. Encerrando.")
        return

    estado         = carregar_estado()
    aba_index      = estado["aba_index"]
    cursor         = estado["cursor"]
    total_gravados = estado["total_gravados"]

    if aba_index >= len(ABAS):
        log.info("✅ Já processado tudo. Nada a fazer.")
        return

    log.info("Lendo leads_validados para deduplicação...")
    numeros_existentes = ler_numeros_validados(planilha)
    vistos_sessao = set()

    # Estatísticas da sessão
    data_inicio     = datetime.now()
    total_lidos     = 0
    total_com_wpp   = 0
    gravados_sessao = 0
    tempos_lote     = []
    por_nicho       = {k: 0 for k in ["food","beleza","saude","varejo","automotivo","educacao","servicos","entretenimento"]}
    num_lote        = 0

    try:
        while aba_index < len(ABAS) and not _shutdown:
            nome_aba = ABAS[aba_index]
            log.info(f"\n{'─'*50}")
            log.info(f"Processando: {nome_aba} (cursor={cursor})")

            registros = ler_aba(planilha, nome_aba)

            if not registros:
                log.info(f"Aba '{nome_aba}' vazia. Avançando.")
                aba_index += 1
                cursor = 0
                salvar_estado({"aba_index": aba_index, "cursor": cursor, "total_gravados": total_gravados})
                continue

            total_lotes = (len(registros) // LOTE_SIZE) + 1

            while cursor < len(registros) and not _shutdown:
                aguardar_horario()
                if _shutdown:
                    break

                t_inicio_lote = time.time()
                num_lote += 1

                lote, novo_cursor = preparar_lote(
                    registros, cursor, nome_aba, numeros_existentes, vistos_sessao
                )
                cursor = novo_cursor
                total_lidos += len(lote)

                if not lote:
                    continue

                wpp_map = verificar_whatsapp([l["numero"] for l in lote])

                com_wpp = []
                for lead in lote:
                    digits = re.sub(r"\D", "", lead["numero"])
                    if wpp_map.get(digits) is True:
                        numeros_existentes.add(digits)
                        com_wpp.append(lead)
                        por_nicho[lead["nicho_group"]] = por_nicho.get(lead["nicho_group"], 0) + 1

                total_com_wpp   += len(com_wpp)
                gravados_sessao += len(com_wpp)

                if com_wpp:
                    gravar_leads_validados(planilha, com_wpp)
                    total_gravados += len(com_wpp)

                salvar_estado({"aba_index": aba_index, "cursor": cursor, "total_gravados": total_gravados})

                # Tempo do lote
                t_lote = time.time() - t_inicio_lote
                tempos_lote.append(t_lote)
                media_t = sum(tempos_lote) / len(tempos_lote)
                lotes_restantes = total_lotes - num_lote
                est_min = int((lotes_restantes * media_t) / 60)

                log.info(
                    f"[PROGRESSO] Lote {num_lote}/{total_lotes} | "
                    f"Com WPP: {len(com_wpp)} | "
                    f"Gravados sessão: {gravados_sessao} | "
                    f"Total acumulado: {total_gravados} | "
                    f"Estimativa restante: ~{est_min} min"
                )

                delay = DELAY_ENTRE_LOTES + random.randint(0, DELAY_JITTER)
                log.info(f"Aguardando {delay}s...")
                time.sleep(delay)

            if not _shutdown:
                log.info(f"✅ Aba '{nome_aba}' concluída.")
                aba_index += 1
                cursor = 0
                salvar_estado({"aba_index": aba_index, "cursor": cursor, "total_gravados": total_gravados})

    finally:
        # Salva relatório sempre — mesmo em caso de Ctrl+C
        data_fim = datetime.now()
        duracao  = int((data_fim - data_inicio).total_seconds() / 60)
        taxa_wpp = f"{round(total_com_wpp / total_lidos * 100, 1)}%" if total_lidos > 0 else "0%"

        relatorio = {
            "data_inicio":       data_inicio.strftime("%d/%m/%Y %H:%M:%S"),
            "data_fim":          data_fim.strftime("%d/%m/%Y %H:%M:%S"),
            "duracao_minutos":   duracao,
            "total_lidos":       total_lidos,
            "total_com_wpp":     total_com_wpp,
            "total_gravados":    total_gravados,
            "taxa_wpp":          taxa_wpp,
            "por_nicho_group":   por_nicho,
        }
        salvar_relatorio(relatorio)

        if _shutdown:
            log.info("Script encerrado pelo usuário. Estado salvo.")
        else:
            log.info("\n" + "=" * 60)
            log.info("🎉 PROCESSAMENTO CONCLUÍDO!")
            log.info(f"Total gravados em leads_validados: {total_gravados}")
            log.info(f"Taxa de WhatsApp: {taxa_wpp}")
            log.info("=" * 60)


if __name__ == "__main__":
    main()
