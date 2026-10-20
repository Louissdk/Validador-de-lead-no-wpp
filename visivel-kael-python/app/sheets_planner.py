from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import settings
from .normalization import normalize_consent, normalize_phone

log = logging.getLogger("kael.sheets_planner")

OUTPUT_HEADERS = [
    "telefone", "nome", "nicho_original", "categoria_nicho", "endereco", "rating",
    "site_identificado_na_fonte", "perfil_lead", "score", "prioridade_comercial",
    "produto_recomendado", "justificativa_qualificacao",
    "paragrafo_1_abertura", "paragrafo_2_contexto", "paragrafo_3_proposta",
    "paragrafo_4_chamada", "mensagem_completa_copiar_colar",
    "status_autorizacao_whatsapp", "origem_opt_in", "data_opt_in",
    "status_revisao", "proxima_acao", "nota_revisor", "analisado_em",
]
CHUNK_SIZE = 400

NICHE_LABELS = {
    "food": "Alimentação",
    "beleza": "Beleza e bem-estar",
    "saude": "Saúde",
    "automotivo": "Automotivo",
    "educacao": "Educação",
    "servicos": "Serviços profissionais",
    "entretenimento": "Entretenimento",
    "varejo": "Varejo / outros",
}


def _norm_header(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def _row_map(row: dict) -> dict[str, Any]:
    return {_norm_header(key): value for key, value in row.items()}


def _first(row: dict[str, Any], *keys: str, default: Any = "") -> Any:
    normalized = _row_map(row)
    for key in keys:
        value = normalized.get(_norm_header(key))
        if value is not None and str(value).strip() != "":
            return value
    return default


def _is_true(value: Any) -> bool | None:
    text = str(value or "").strip().lower()
    if text in {"true", "sim", "yes", "1", "com site", "tem site", "verdadeiro"}:
        return True
    if text in {"false", "nao", "não", "no", "0", "sem site", "nao tem site", "não tem site", "falso"}:
        return False
    return None


def classify_niche(niche: str, explicit_group: str = "") -> str:
    group = _norm_header(explicit_group)
    if group in NICHE_LABELS:
        return group
    n = _norm_header(niche)
    rules = {
        "food": ("pizzaria", "restaurante", "lanch", "hamburg", "cafe", "padaria", "confeitaria", "acai", "sorvete", "doceria", "delivery", "marmita", "buffet", "cantina", "esfiaria"),
        "beleza": ("barbearia", "beleza", "estetica", "tatuagem", "depil", "salao", "manicure", "cabeleireiro", "sobrancelha", "podologia", "spa", "nail"),
        "saude": ("odontologia", "dentista", "clinica", "psicolog", "fisioter", "nutricion", "farmacia", "academia", "medico", "hospital", "laboratorio", "veterin", "pet", "terapia"),
        "automotivo": ("concessionaria", "mecanica", "borracharia", "lava_jato", "funilaria", "guincho", "oficina", "pneu", "autopecas", "insulfilm"),
        "educacao": ("escola", "curso", "faculdade", "creche", "idioma", "reforco", "colegio", "ensino", "ingles", "musica", "danca"),
        "servicos": ("serralheria", "eletricista", "encanador", "dedetiz", "imobiliaria", "advocacia", "contabilidade", "limpeza", "reforma", "chaveiro", "fotografia", "ar_condicionado"),
        "entretenimento": ("bar", "boate", "clube", "show", "evento", "festa", "karaoke", "brinquedoteca"),
    }
    for category, keywords in rules.items():
        if any(word in n for word in keywords):
            return category
    return "varejo"


def priority_from_score(score: int) -> str:
    if score >= 75:
        return "quente"
    if score >= 50:
        return "morno"
    return "baixo"


def _score(row: dict[str, Any], niche_group: str, site: bool | None) -> tuple[int, str]:
    raw = _first(row, "score")
    try:
        return max(0, min(100, int(float(raw)))), "score existente no qualificador"
    except (ValueError, TypeError):
        score = 50
        rating_raw = _first(row, "rating", "avaliacao")
        try:
            rating = float(rating_raw)
            if rating >= 4.7:
                score += 25
            elif rating >= 4.5:
                score += 20
            elif rating >= 4.0:
                score += 15
            elif rating >= 3.5:
                score += 8
            elif rating == 0:
                score -= 5
            elif rating < 3.0:
                score -= 15
        except (ValueError, TypeError):
            score -= 5
        if site is False:
            score += 20
        elif site is True:
            score -= 5
        if niche_group in {"food", "beleza"}:
            score += 15
        elif niche_group == "saude":
            score += 10
        elif niche_group == "varejo":
            score += 5
        elif niche_group in {"automotivo", "servicos"}:
            score += 3
        return max(0, min(100, score)), "score estimado por regras simples; validar antes de priorizar"


def recommend_product(niche_group: str, site: bool | None) -> tuple[str, str]:
    without_site = {
        "food": "Cardápio digital / site com informações de atendimento",
        "beleza": "Site de serviços, portfólio e informações de agendamento",
        "saude": "Site institucional com serviços e canais de contato/agendamento",
        "automotivo": "Site de serviços com informações e solicitação de orçamento",
        "educacao": "Página de cursos/serviços e captação de interessados",
        "servicos": "Site institucional com formulário de solicitação de orçamento",
        "entretenimento": "Página de eventos/serviços com informações de contato",
        "varejo": "Catálogo digital ou site institucional",
    }
    with_site = {
        "food": "Diagnóstico do cardápio digital e dos caminhos para contato/pedido",
        "beleza": "Diagnóstico da apresentação dos serviços e da jornada de agendamento",
        "saude": "Diagnóstico informativo da presença digital e dos canais de contato",
        "automotivo": "Diagnóstico da apresentação dos serviços e captação de orçamentos",
        "educacao": "Diagnóstico da apresentação de cursos e captação de interessados",
        "servicos": "Diagnóstico de presença digital e captação de solicitações",
        "entretenimento": "Diagnóstico da apresentação de eventos/serviços e contato",
        "varejo": "Diagnóstico de catálogo digital e jornada de contato",
    }
    if site is False:
        return without_site.get(niche_group, without_site["varejo"]), "A fonte não apresentou um site associado; confirmar manualmente antes de afirmar ausência."
    if site is True:
        return with_site.get(niche_group, with_site["varejo"]), "A fonte apresentou um site; qualidade ou necessidade de reformulação não foram verificadas."
    return "Diagnóstico de presença digital (validar manualmente)", "A informação sobre site está ausente ou inconclusiva."


def _message_blocks(lead_name: str, company: str, niche: str, product: str, site: bool | None) -> tuple[str, str, str, str, str]:
    opening = f"Olá, {lead_name}! Tudo bem? Sou da Visível."
    if site is False:
        context = (
            f"A fonte consultada não trouxe um site associado à {company}. "
            "Essa informação precisa ser confirmada, então não estou presumindo que a empresa não tenha site."
        )
    elif site is True:
        context = (
            f"A fonte consultada indica um site associado à {company}. "
            "Sem uma análise do site, não seria correto afirmar que há algum problema."
        )
    else:
        context = (
            f"Tenho o segmento de atuação da {company} como {niche}, mas não tenho informação suficiente "
            "para avaliar a presença digital dela."
        )
    proposal = f"Uma possibilidade a avaliar seria: {product}. A recomendação é preliminar e depende das necessidades reais da empresa."
    cta = (
        "Se fizer sentido e você tiver autorizado receber mensagens da Visível por WhatsApp, "
        "posso compartilhar uma sugestão objetiva. Se não quiser receber comunicações, é só nos avisar."
    )
    complete = "\n\n".join((opening, context, proposal, cta))
    return opening, context, proposal, cta, complete


def plan_lead(source: dict[str, Any], analyzed_at: str | None = None) -> dict[str, Any]:
    phone = normalize_phone(_first(source, "telefone", "numero", "phone", "phoneNumber"))
    if not phone:
        raise ValueError("Telefone inválido")
    name = str(_first(source, "nome", "name", "title")).strip()
    niche = str(_first(source, "nicho", "niche", "type", default="")).strip()
    niche_group = classify_niche(niche, str(_first(source, "nicho_group", "nicho_grouping", default="")))
    site = _is_true(_first(source, "tem_site", "site_identificado", "has_site", default=""))
    score, score_basis = _score(source, niche_group, site)
    priority = str(_first(source, "priority_level", "prioridade_comercial", default="")).strip().lower()
    if priority not in {"quente", "morno", "baixo"}:
        priority = priority_from_score(score)
    product, evidence = recommend_product(niche_group, site)
    address = str(_first(source, "endereco", "address", "cidade", default="")).strip()
    rating = str(_first(source, "rating", "avaliacao", default="")).strip()

    consent, consent_source, consent_at = normalize_consent(
        _first(source, "consent_status", "status_autorizacao_whatsapp", default="unknown"),
        _first(source, "consent_source", "origem_opt_in", default=""),
        _first(source, "consent_at", "data_opt_in", default=""),
    )
    contact_status = str(_first(source, "status_contato", "status_whatsapp", "status_revisao", "status_autorizacao_whatsapp", default="")).strip().lower()
    replied_value = str(_first(source, "replied_at", "respondeu", "resposta_recebida", default="")).strip().lower()
    replied = replied_value not in {"", "false", "nao", "não", "no", "0", "none"}
    suppressed = consent == "opt_out" or any(term in contact_status for term in ("opt_out", "descadastro", "nao_contatar", "não contatar", "bloqueado"))
    if suppressed:
        consent = "opt_out"

    profile = (
        "Sem site identificado na fonte (confirmar)" if site is False
        else "Site informado pela fonte (qualidade não auditada)" if site is True
        else "Presença digital não verificada"
    )
    category_label = NICHE_LABELS[niche_group]
    rationale = f"Categoria: {category_label}. {evidence} {score_basis}; score {score}/100 ({priority})."
    stamp = analyzed_at or datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    opening = context = proposal = cta = complete = ""
    consent_ok = consent == "opt_in" and bool(consent_source and consent_at) and not suppressed and not replied and bool(name)
    if consent_ok:
        company = str(_first(source, "empresa", "company", "nome_empresa", default=name)).strip() or name
        opening, context, proposal, cta, complete = _message_blocks(name, company, niche or category_label, product, site)
        status = "PENDENTE_REVISAO"
        next_action = "Revisar mensagem, dados e autorização antes de copiar e colar."
    elif suppressed:
        status = "BLOQUEADO_DESCADASTRO"
        next_action = "Não contatar. Manter bloqueio."
    elif replied:
        status = "BLOQUEADO_JA_RESPONDEU"
        next_action = "Revisar a conversa manualmente; não iniciar nova abordagem automaticamente."
    elif not name:
        status = "REVISAR_DADOS"
        next_action = "Corrigir ou confirmar nome da empresa."
    else:
        status = "AGUARDAR_AUTORIZACAO"
        next_action = "Não gerar mensagem de WhatsApp: registrar autorização válida antes de abordar neste canal."

    return {
        "telefone": phone,
        "nome": name,
        "nicho_original": niche,
        "categoria_nicho": category_label,
        "endereco": address,
        "rating": rating,
        "site_identificado_na_fonte": "Sim" if site is True else "Não" if site is False else "Não verificado",
        "perfil_lead": profile,
        "score": score,
        "prioridade_comercial": priority,
        "produto_recomendado": product,
        "justificativa_qualificacao": rationale,
        "paragrafo_1_abertura": opening,
        "paragrafo_2_contexto": context,
        "paragrafo_3_proposta": proposal,
        "paragrafo_4_chamada": cta,
        "mensagem_completa_copiar_colar": complete,
        "status_autorizacao_whatsapp": consent or "unknown",
        "origem_opt_in": consent_source or "",
        "data_opt_in": consent_at or "",
        "status_revisao": status,
        "proxima_acao": next_action,
        "nota_revisor": str(_first(source, "nota_revisor", "observacao", default="")).strip(),
        "analisado_em": stamp,
        "_consent_ok": consent_ok,
    }


def _connect_sheet():
    try:
        import gspread
        from google.oauth2.service_account import Credentials
    except ImportError as exc:
        raise RuntimeError("Instale gspread e google-auth: python -m pip install gspread google-auth") from exc

    if not settings.google_sheets_id:
        raise RuntimeError("Preencha GOOGLE_SHEETS_ID no arquivo .env.")
    credentials_path = Path(settings.google_creds_file).expanduser()
    if not credentials_path.is_absolute():
        credentials_path = Path.cwd() / credentials_path
    if not credentials_path.is_file():
        raise RuntimeError(
            f"Não encontrei as credenciais do Google em {credentials_path}. "
            "Coloque o JSON da Service Account localmente; não o envie ao GitHub."
        )
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    credentials = Credentials.from_service_account_file(str(credentials_path), scopes=scopes)
    client = gspread.authorize(credentials)
    spreadsheet = client.open_by_key(settings.google_sheets_id)
    log.info("Conectado à planilha Google: %s", spreadsheet.title)
    return spreadsheet


def _values_to_dicts(values: list[list[str]]) -> list[dict[str, str]]:
    if not values:
        return []
    headers = values[0]
    records = []
    for raw in values[1:]:
        if not any(str(value).strip() for value in raw):
            continue
        record = {}
        for idx, header in enumerate(headers):
            key = str(header).strip()
            if key:
                record[key] = raw[idx] if idx < len(raw) else ""
        records.append(record)
    return records


def _rows_preserving_review(existing_rows: list[dict[str, str]], planned_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    old_by_phone = {}
    for row in existing_rows:
        phone = normalize_phone(_first(row, "telefone", "numero", "phone"))
        if phone:
            old_by_phone[phone] = row
    merged: list[dict[str, Any]] = []
    human_fields = {
        "paragrafo_1_abertura", "paragrafo_2_contexto", "paragrafo_3_proposta",
        "paragrafo_4_chamada", "mensagem_completa_copiar_colar", "status_revisao", "nota_revisor",
    }
    for planned in planned_rows:
        item = dict(planned)
        phone = item["telefone"]
        old = old_by_phone.get(phone)
        if old:
            old_norm = _row_map(old)
            old_status = str(old_norm.get(_norm_header("status_revisao"), "")).strip().upper()
            old_consent = str(old_norm.get(_norm_header("status_autorizacao_whatsapp"), "")).strip().lower()
            old_suppressed = old_consent == "opt_out" or any(term in old_status.lower() for term in ("descadastro", "nao_contatar", "não contatar"))
            old_replied = "BLOQUEADO_JA_RESPONDEU" in old_status
            # Keep the strongest previously recorded restriction even if a later import
            # brings a stale source row back as active/opt-in.
            if old_suppressed:
                item["status_autorizacao_whatsapp"] = "opt_out"
                item["status_revisao"] = "BLOQUEADO_DESCADASTRO"
                item["proxima_acao"] = "Não contatar. Manter bloqueio."
                item["_consent_ok"] = False
            elif old_replied:
                item["status_revisao"] = "BLOQUEADO_JA_RESPONDEU"
                item["proxima_acao"] = "Resposta registrada anteriormente; revisar manualmente."
                item["_consent_ok"] = False
            if item.get("_consent_ok"):
                for field in human_fields:
                    previous = old_norm.get(field)
                    if previous not in (None, ""):
                        item[field] = previous
            else:
                # Never retain an old copy-ready message if authorization is missing/revoked.
                for field in human_fields - {"status_revisao", "nota_revisor"}:
                    item[field] = ""
        merged.append(item)
    planned_phones = {item["telefone"] for item in planned_rows}
    for phone, old in old_by_phone.items():
        if phone in planned_phones:
            continue
        normalized = _row_map(old)
        carry = {header: normalized.get(_norm_header(header), "") for header in OUTPUT_HEADERS}
        if carry.get("telefone"):
            merged.append(carry)
    return merged


def run_plan_sheet(*, write: bool = False, source_tab: str | None = None, output_tab: str | None = None, limit: int = 0) -> dict[str, int]:
    """Read the existing qualifier tab and plan rows; write only when --write is passed."""
    if limit < 0 or limit > 100000:
        raise ValueError("--limit deve ser 0 (todos) ou um número entre 1 e 100000.")
    source_tab = (source_tab or settings.google_source_tab).strip()
    output_tab = (output_tab or settings.google_output_tab).strip()
    if not source_tab or not output_tab or source_tab == output_tab:
        raise ValueError("Aba de origem e aba de saída precisam ser diferentes e não vazias.")

    spreadsheet = _connect_sheet()
    try:
        source_ws = spreadsheet.worksheet(source_tab)
    except Exception as exc:
        raise RuntimeError(f"Não encontrei a aba de origem '{source_tab}'. Confirme o nome na planilha.") from exc
    source_rows = source_ws.get_all_records(head=1, default_blank="")
    if limit:
        source_rows = source_rows[:limit]
    if not source_rows:
        raise RuntimeError(f"A aba '{source_tab}' não contém registros após o cabeçalho.")

    old_rows: list[dict[str, str]] = []
    try:
        output_ws = spreadsheet.worksheet(output_tab)
        old_rows = _values_to_dicts(output_ws.get_all_values())
    except Exception:
        output_ws = None

    timestamp = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    planned_rows = []
    stats = {
        "lidos": 0, "planejados": 0, "com_mensagem": 0,
        "sem_autorizacao": 0, "descadastro": 0, "resposta_registrada": 0,
        "telefone_invalido": 0, "campos_incompletos": 0,
    }
    for row in source_rows:
        stats["lidos"] += 1
        try:
            planned = plan_lead(row, analyzed_at=timestamp)
        except ValueError:
            stats["telefone_invalido"] += 1
            continue
        stats["planejados"] += 1
        if planned["mensagem_completa_copiar_colar"]:
            stats["com_mensagem"] += 1
        elif planned["status_revisao"] == "BLOQUEADO_DESCADASTRO":
            stats["descadastro"] += 1
        elif planned["status_revisao"] == "BLOQUEADO_JA_RESPONDEU":
            stats["resposta_registrada"] += 1
        else:
            stats["sem_autorizacao"] += 1
        if not planned["nome"] or not planned["nicho_original"]:
            stats["campos_incompletos"] += 1
        planned_rows.append(planned)

    print(f"Planilha: {spreadsheet.title}")
    print(f"Aba de origem: {source_tab} | aba de saída: {output_tab}")
    print("Prévia: nada será gravado." if not write else "Modo de gravação: atualizar a aba de saída; a aba de origem não será alterada.")
    print("Resumo:", ", ".join(f"{key}={value}" for key, value in stats.items()))
    for row in planned_rows[:3]:
        print("-" * 70)
        print(f"{row['nome']} | {row['telefone']} | {row['categoria_nicho']} | score {row['score']} ({row['prioridade_comercial']})")
        print(f"Produto sugerido: {row['produto_recomendado']}")
        print(f"Status: {row['status_revisao']} | Próxima ação: {row['proxima_acao']}")
        if row["mensagem_completa_copiar_colar"]:
            print("Prévia da mensagem:\n" + row["mensagem_completa_copiar_colar"])

    if not write:
        print("Para gravar a nova aba, repita o comando com --write.")
        return stats

    merged = _rows_preserving_review(old_rows, planned_rows)
    if output_ws is None:
        output_ws = spreadsheet.add_worksheet(title=output_tab, rows=max(100, len(merged) + 1), cols=len(OUTPUT_HEADERS))
    output_ws.resize(rows=max(2, len(merged) + 1), cols=len(OUTPUT_HEADERS))
    matrix = [OUTPUT_HEADERS]
    for item in merged:
        matrix.append([str(item.get(header, "") if item.get(header, "") is not None else "") for header in OUTPUT_HEADERS])
    for start in range(0, len(matrix), CHUNK_SIZE):
        chunk = matrix[start:start + CHUNK_SIZE]
        start_row = start + 1
        end_row = start + len(chunk)
        output_ws.update(
            range_name=f"A{start_row}:X{end_row}",
            values=chunk,
            value_input_option="RAW",
        )
    log.info("Aba '%s' atualizada com %s linhas; origem preservada.", output_tab, len(merged))
    print(f"Gravação concluída: {len(merged)} linha(s) na aba '{output_tab}'. A aba '{source_tab}' não foi alterada.")
    print("Lembrete: mensagem preenchida só aparece para contato com opt-in documentado.")
    return stats
