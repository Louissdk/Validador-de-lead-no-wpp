"""Continuous local lead-validation and draft-planning worker.

This worker never sends WhatsApp messages. It reads a source sheet, builds the
consolidation preview in memory, qualifies each group with the existing planner,
blocks drafts for ambiguous consolidation groups, and writes only to a separate
planning sheet when --write is explicitly supplied.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

if os.name != "nt":
    import fcntl

from .config import settings
from .lead_consolidation import build_consolidation_preview
from .normalization import normalize_consent, normalize_phone
from .sheets_planner import (
    CHUNK_SIZE,
    NICHE_LABELS,
    OUTPUT_HEADERS,
    _business_review_key,
    _first,
    _message_blocks,
    _is_true,
    _rows_preserving_review,
    _score,
    _values_to_dicts,
    classify_niche,
    plan_lead,
    priority_from_score,
    recommend_product,
)

log = logging.getLogger("kael.lead_worker")
DEFAULT_OUTPUT_TAB = "kael_planejamento_continuo"
MIN_INTERVAL_SECONDS = 60
MAX_INTERVAL_SECONDS = 86400
WORKER_EXTRA_HEADERS = [
    "id_registro_worker",
    "status_consolidacao_v5",
    "grupo_nome_endereco",
    "linhas_origem_revalidacao",
    "campos_originais_com_variantes",
    "variantes_campos_originais",
    "rascunho_comercial_interno",
    "status_rascunho_interno",
]
MESSAGE_FIELDS = (
    "paragrafo_1_abertura",
    "paragrafo_2_contexto",
    "paragrafo_3_proposta",
    "paragrafo_4_chamada",
    "mensagem_completa_copiar_colar",
)
AMBIGUOUS_STATUSES = {
    "DUPLICATA_CANDIDATA",
    "CANDIDATO_COM_CONFLITOS",
    "REVISAR_IDENTIDADE",
    "IDENTIDADE_INCOMPLETA_REVISAR",
    "REVISAR_MULTIPLOS_TELEFONES",
}


class WorkerStopError(RuntimeError):
    """A configuration/safety issue that should stop the worker."""


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _fingerprint(values: list[list[Any]]) -> str:
    return hashlib.sha256(_canonical_json(values).encode("utf-8")).hexdigest()


def _worker_id(row: dict[str, Any]) -> str:
    material = "|".join((
        str(row.get("grupo_nome_endereco", "")),
        normalize_phone(_first(row, "telefone_normalizado", "telefone", "phoneNumber", "phone", "numero")),
        str(row.get("linhas_origem_revalidacao", "")),
        str(row.get("linha_representada", "")),
    ))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _normalize_raw_rows(values: list[list[Any]]) -> list[dict[str, str]]:
    if not values:
        return []
    headers = [str(value or "").strip() for value in values[0]]
    result: list[dict[str, str]] = []
    for raw in values[1:]:
        row = [str(value or "") for value in raw[:len(headers)]]
        row.extend([""] * (len(headers) - len(row)))
        if not any(value.strip() for value in row):
            continue
        result.append({header: row[index] for index, header in enumerate(headers) if header})
    return result


def _review_only_row(source: dict[str, Any], analyzed_at: str) -> dict[str, Any]:
    """Build a useful review row even if plan_lead rejects a missing/invalid phone."""
    item: dict[str, Any] = {header: "" for header in OUTPUT_HEADERS}
    raw_phone = str(_first(source, "telefone_normalizado", "telefone", "numero", "phone", "phoneNumber", "whatsapp", default="") or "").strip()
    phone = normalize_phone(raw_phone)
    name = str(_first(source, "nome", "name", "title", "empresa", "company", default="") or "").strip()
    niche = str(_first(source, "nicho", "niche", "type", "categoria", "segmento", default="") or "").strip()
    address = str(_first(source, "endereco", "address", "location", default="") or "").strip()
    rating = str(_first(source, "rating", "avaliacao", default="") or "").strip()
    explicit_group = str(_first(source, "nicho_group", "nicho_grouping", default="") or "")
    niche_group = classify_niche(niche, explicit_group)
    site = _is_true(_first(source, "tem_site", "site_identificado_na_fonte", "site_identificado", "has_site", default=""))
    if site is None and str(_first(source, "website", "site", "url", "dominio", default="") or "").strip():
        site = True
    score, rationale = _score(source, niche_group, site)
    product, evidence = recommend_product(niche_group, site)
    consent, consent_source, consent_at = normalize_consent(
        _first(source, "consent_status", "status_autorizacao_whatsapp", default="unknown"),
        _first(source, "consent_source", "origem_opt_in", default=""),
        _first(source, "consent_at", "data_opt_in", default=""),
    )
    if consent == "opt_out":
        review_status = "BLOQUEADO_DESCADASTRO"
        next_action = "Não contatar. Manter o bloqueio de descadastro."
    elif not raw_phone:
        review_status = "SEM_TELEFONE_NA_ORIGEM"
        next_action = "Buscar o telefone em fonte confiável ou manter para revisão manual."
    else:
        review_status = "TELEFONE_NAO_NORMALIZADO"
        next_action = "Revisar o valor original do telefone; não completar por suposição."

    item.update({
        "telefone": phone,
        "nome": name,
        "nicho_original": niche,
        "categoria_nicho": NICHE_LABELS.get(niche_group, NICHE_LABELS["varejo"]),
        "endereco": address,
        "rating": rating,
        "site_identificado_na_fonte": "Sim" if site is True else "Não" if site is False else "Não verificado",
        "perfil_lead": "Dados insuficientes para validação de contato",
        "score": score,
        "prioridade_comercial": priority_from_score(score),
        "produto_recomendado": product,
        "justificativa_qualificacao": f"{rationale}; {evidence}",
        "status_autorizacao_whatsapp": consent or "unknown",
        "origem_opt_in": consent_source or "",
        "data_opt_in": consent_at or "",
        "status_revisao": review_status,
        "proxima_acao": next_action,
        "nota_revisor": str(_first(source, "nota_revisor", "observacao", default="") or "").strip(),
        "analisado_em": analyzed_at,
        "_consent_ok": False,
    })
    return item


def _prepare_internal_draft(source: dict[str, Any], item: dict[str, Any], consolidation_status: str) -> tuple[str, str]:
    """Create an internal-only draft without treating it as consent or authorization."""
    review_status = str(item.get("status_revisao", "") or "")
    consent = str(item.get("status_autorizacao_whatsapp", "unknown") or "unknown").strip().lower()
    contact_status = str(_first(
        source, "status_contato", "status_whatsapp", "status_revisao",
        "status_autorizacao_whatsapp", default=""
    ) or "").strip().lower()
    replied_value = str(_first(
        source, "replied_at", "respondeu", "resposta_recebida", default=""
    ) or "").strip().lower()
    replied = replied_value not in {"", "false", "nao", "não", "no", "0", "none"}
    suppressed = consent == "opt_out" or any(
        term in contact_status
        for term in ("opt_out", "descadastro", "nao_contatar", "não contatar", "bloqueado")
    )

    # These checks also apply to rows with invalid/missing phones, which bypass plan_lead.
    if suppressed or review_status == "BLOQUEADO_DESCADASTRO":
        item["status_autorizacao_whatsapp"] = "opt_out"
        item["status_revisao"] = "BLOQUEADO_DESCADASTRO"
        item["proxima_acao"] = "Não contatar. Manter bloqueio de descadastro/supressão."
        return "", "BLOQUEADO_DESCADASTRO"
    if replied or review_status == "BLOQUEADO_JA_RESPONDEU":
        item["status_revisao"] = "BLOQUEADO_JA_RESPONDEU"
        item["proxima_acao"] = "Resposta anterior identificada; não iniciar nova abordagem."
        return "", "BLOQUEADO_JA_RESPONDEU"

    name = str(item.get("nome", "") or "").strip()
    niche = str(item.get("nicho_original", "") or "").strip()
    product = str(item.get("produto_recomendado", "") or "").strip()
    if not name or not niche or not product:
        return "", "SEM_DADOS_PARA_RASCUNHO"

    # Approved copy-ready messages stay in the existing human-review fields.
    if item.get("_consent_ok") and consolidation_status == "SEM_DUPLICATA_EXATA_IDENTIFICADA":
        return "", "MENSAGEM_AUTORIZADA_SEPARADA_PARA_REVISAO"

    company = str(_first(source, "empresa", "company", "nome_empresa", "title", "name", default=name) or name).strip() or name
    site_label = str(item.get("site_identificado_na_fonte", "") or "").strip().lower()
    site = True if site_label in {"sim", "yes", "true"} else False if site_label in {"não", "nao", "no", "false"} else None
    opening, context, proposal, _cta, _complete = _message_blocks(name, company, niche, product, site)
    warning = (
        "[RASCUNHO INTERNO — NÃO COPIAR/ENVIAR. Confirmar identidade, elegibilidade e autorização documentada antes de qualquer contato.]"
    )
    draft = "\n\n".join((opening, context, proposal, warning))

    if consolidation_status != "SEM_DUPLICATA_EXATA_IDENTIFICADA" or review_status == "REVISAR_CONSOLIDACAO":
        status = "REVISAR_IDENTIDADE_E_AUTORIZACAO"
    elif not str(item.get("telefone", "") or "").strip():
        status = "REVISAR_TELEFONE_E_AUTORIZACAO"
    else:
        status = "REVISAR_AUTORIZACAO_ANTES_DE_CONTATO"
    return draft, status


def _plan_consolidated_values(values: list[list[Any]], analyzed_at: str | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Consolidate in memory, qualify each group, and gate drafts on identity status."""
    matrix, consolidation_stats = build_consolidation_preview(values)
    if len(matrix) < 2:
        raise ValueError("A origem não contém registros para processar.")
    headers = matrix[0]
    index = {header: i for i, header in enumerate(headers)}
    required = {"status_consolidacao_v5", "grupo_nome_endereco", "linhas_origem_revalidacao"}
    if not required.issubset(index):
        raise WorkerStopError(f"Prévia de consolidação sem campos obrigatórios: {sorted(required - set(index))}")

    timestamp = analyzed_at or datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    planned: list[dict[str, Any]] = []
    review_counts: dict[str, int] = {}
    stats: dict[str, Any] = {
        "linhas_consolidadas": len(matrix) - 1,
        "planejados": 0,
        "com_mensagem": 0,
        "sem_autorizacao": 0,
        "descadastro": 0,
        "resposta_registrada": 0,
        "telefone_invalido": 0,
        "sem_telefone": 0,
        "revisao_consolidacao": 0,
        "campos_incompletos": 0,
        "mensagens_bloqueadas_por_identidade": 0,
        "rascunhos_internos": 0,
        "rascunhos_revisar_autorizacao": 0,
        "rascunhos_revisar_identidade": 0,
        "rascunhos_revisar_telefone": 0,
        "rascunhos_sem_dados": 0,
        "rascunhos_bloqueados_descadastro": 0,
        "rascunhos_bloqueados_resposta": 0,
    }

    for values_row in matrix[1:]:
        source = {header: values_row[i] if i < len(values_row) else "" for i, header in enumerate(headers)}
        consolidation_status = str(source.get("status_consolidacao_v5", "")).strip()
        try:
            item = plan_lead(source, analyzed_at=timestamp)
            stats["planejados"] += 1
        except ValueError:
            item = _review_only_row(source, timestamp)
            if item["status_revisao"] == "SEM_TELEFONE_NA_ORIGEM":
                stats["sem_telefone"] += 1
            else:
                stats["telefone_invalido"] += 1

        if not item.get("nome") or not item.get("nicho_original"):
            stats["campos_incompletos"] += 1

        # Ambiguous identity groups can be analyzed, but no copy-ready message
        # is allowed until their consolidation/review status is resolved.
        if consolidation_status != "SEM_DUPLICATA_EXATA_IDENTIFICADA":
            previous_status = str(item.get("status_revisao", ""))
            if previous_status not in {"BLOQUEADO_DESCADASTRO", "BLOQUEADO_JA_RESPONDEU"}:
                item["status_revisao"] = "REVISAR_CONSOLIDACAO"
                item["proxima_acao"] = str(source.get("acao_sugerida", "") or "Revisar identidade, duplicidade e conflitos antes de preparar contato.")
            for field in MESSAGE_FIELDS:
                item[field] = ""
            item["_consent_ok"] = False
            stats["revisao_consolidacao"] += 1
            stats["mensagens_bloqueadas_por_identidade"] += 1

        # Internal draft is separate from copy-ready fields and is never permission to contact.
        internal_draft, internal_status = _prepare_internal_draft(source, item, consolidation_status)
        item["rascunho_comercial_interno"] = internal_draft
        item["status_rascunho_interno"] = internal_status
        if internal_draft:
            stats["rascunhos_internos"] += 1
        if internal_status == "REVISAR_AUTORIZACAO_ANTES_DE_CONTATO":
            stats["rascunhos_revisar_autorizacao"] += 1
        elif internal_status == "REVISAR_IDENTIDADE_E_AUTORIZACAO":
            stats["rascunhos_revisar_identidade"] += 1
        elif internal_status == "REVISAR_TELEFONE_E_AUTORIZACAO":
            stats["rascunhos_revisar_telefone"] += 1
        elif internal_status == "SEM_DADOS_PARA_RASCUNHO":
            stats["rascunhos_sem_dados"] += 1
        elif internal_status == "BLOQUEADO_DESCADASTRO":
            stats["rascunhos_bloqueados_descadastro"] += 1
        elif internal_status == "BLOQUEADO_JA_RESPONDEU":
            stats["rascunhos_bloqueados_resposta"] += 1

        if item.get("mensagem_completa_copiar_colar"):
            stats["com_mensagem"] += 1
        elif item.get("status_revisao") == "BLOQUEADO_DESCADASTRO":
            stats["descadastro"] += 1
        elif item.get("status_revisao") == "BLOQUEADO_JA_RESPONDEU":
            stats["resposta_registrada"] += 1
        elif item.get("status_revisao") == "AGUARDAR_AUTORIZACAO":
            stats["sem_autorizacao"] += 1

        item.update({
            "id_registro_worker": _worker_id(source),
            "status_consolidacao_v5": consolidation_status,
            "grupo_nome_endereco": str(source.get("grupo_nome_endereco", "")),
            "linhas_origem_revalidacao": str(source.get("linhas_origem_revalidacao", "")),
            "campos_originais_com_variantes": str(source.get("campos_originais_com_variantes", source.get("campos_negocio_conflitantes", "")) or ""),
            "variantes_campos_originais": str(source.get("variantes_campos_originais", "") or ""),
        })
        review_counts[consolidation_status] = review_counts.get(consolidation_status, 0) + 1
        planned.append(item)

    stats["status_consolidacao"] = dict(sorted(review_counts.items()))
    stats["grupos_identidade"] = consolidation_stats.get("grupos_identidade", 0)
    stats["linhas_origem"] = consolidation_stats.get("linhas_origem", 0)
    stats["linhas_origem_referenciadas"] = consolidation_stats.get("linhas_origem_referenciadas", 0)
    stats["grupos_com_conflitos"] = consolidation_stats.get("grupos_com_conflitos", 0)
    stats["telefones_compartilhados_distintos"] = consolidation_stats.get("telefones_compartilhados_distintos", 0)
    return planned, stats


def _lock_file(path: Path):
    @contextmanager
    def manager() -> Iterator[None]:
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = path.open("a+b")
        try:
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                if handle.read(1) == b"":
                    handle.seek(0)
                    handle.write(b"0")
                    handle.flush()
                handle.seek(0)
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError as exc:
                    raise WorkerStopError("Já existe outra instância do lead-worker ativa.") from exc
            else:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError as exc:
                    raise WorkerStopError("Já existe outra instância do lead-worker ativa.") from exc
            yield
        finally:
            try:
                if os.name == "nt":
                    import msvcrt
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            handle.close()
    return manager()


def _init_state(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("CREATE TABLE IF NOT EXISTS worker_state (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
    conn.commit()
    return conn


def _state_get(conn: sqlite3.Connection, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM worker_state WHERE key=?", (key,)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except (ValueError, TypeError):
        return default


def _state_set(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO worker_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, _canonical_json(value)),
    )


def _read_source_values(spreadsheet: Any, source_tab: str, limit: int) -> list[list[str]]:
    try:
        worksheet = spreadsheet.worksheet(source_tab)
    except Exception as exc:
        if exc.__class__.__name__ == "WorksheetNotFound":
            raise WorkerStopError(
                f"A aba de origem '{source_tab}' não existe. Verifique o nome na planilha."
            ) from exc
        raise
    if not limit:
        values = worksheet.get_all_values()
    else:
        # Read only a bounded range for the initial safe pilot.
        try:
            from gspread.utils import rowcol_to_a1
        except ImportError as exc:
            raise WorkerStopError("gspread.utils indisponível; não é possível limitar a leitura com segurança.") from exc
        headers = worksheet.row_values(1)
        if not headers:
            return []
        last_col = rowcol_to_a1(1, len(headers)).rstrip("1")
        last_row = min(limit + 1, int(worksheet.row_count))
        values = worksheet.get(f"A1:{last_col}{last_row}")
    if not values or not any(str(value or "").strip() for value in values[0]):
        raise WorkerStopError(f"A aba '{source_tab}' não possui cabeçalhos.")
    return values


def _prepare_matrix(planned: list[dict[str, Any]]) -> list[list[str]]:
    headers = OUTPUT_HEADERS + WORKER_EXTRA_HEADERS
    matrix = [headers]
    for item in planned:
        matrix.append([
            str(item.get(header, "") if item.get(header, "") is not None else "")
            for header in headers
        ])
    return matrix


def _meta(row: dict[str, Any]) -> dict[str, str]:
    return {header: str(row.get(header, "") or "") for header in WORKER_EXTRA_HEADERS}


def _merge_worker_rows(old_rows: list[dict[str, str]], planned_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Use existing review-preservation rules and also preserve no-phone review notes."""
    phone_planned = [row for row in planned_rows if normalize_phone(row.get("telefone", ""))]
    no_phone_planned = [row for row in planned_rows if not normalize_phone(row.get("telefone", ""))]

    old_meta_by_identity: dict[tuple[str, str, str], dict[str, str]] = {}
    old_no_phone_by_id: dict[str, dict[str, str]] = {}
    for old in old_rows:
        identity = _business_review_key(old)
        if identity:
            old_meta_by_identity[identity] = _meta(old)
        worker_id = str(old.get("id_registro_worker", "") or "")
        if worker_id and not normalize_phone(old.get("telefone", "")):
            old_no_phone_by_id[worker_id] = old

    current_meta_by_identity: dict[tuple[str, str, str], dict[str, str]] = {}
    for row in phone_planned:
        identity = _business_review_key(row)
        if identity:
            current_meta_by_identity[identity] = _meta(row)

    merged_phone = _rows_preserving_review(old_rows, phone_planned)
    for row in merged_phone:
        worker_id = str(row.get("id_registro_worker", "") or "")
        if worker_id:
            continue
        identity = _business_review_key(row)
        meta = current_meta_by_identity.get(identity) if identity else None
        if meta is None and identity:
            meta = old_meta_by_identity.get(identity)
        if meta:
            row.update(meta)

    merged_no_phone: list[dict[str, Any]] = []
    current_no_phone_ids = set()
    for row in no_phone_planned:
        item = dict(row)
        worker_id = str(item.get("id_registro_worker", "") or "")
        current_no_phone_ids.add(worker_id)
        old = old_no_phone_by_id.get(worker_id)
        if old:
            old_note = str(old.get("nota_revisor", "") or "")
            if old_note:
                item["nota_revisor"] = old_note
        for field in MESSAGE_FIELDS:
            item[field] = ""
        merged_no_phone.append(item)

    # Preserve old no-phone review rows as historical items, rather than silently
    # dropping them when an upstream row disappears or changes identity.
    for worker_id, old in old_no_phone_by_id.items():
        if worker_id in current_no_phone_ids:
            continue
        item = {header: str(old.get(header, "") or "") for header in OUTPUT_HEADERS + WORKER_EXTRA_HEADERS}
        item["status_revisao"] = "ORIGEM_NAO_ENCONTRADA_REVISAR"
        item["proxima_acao"] = "Registro não encontrado na origem atual; revisar histórico antes de arquivar."
        for field in MESSAGE_FIELDS:
            item[field] = ""
        item["rascunho_comercial_interno"] = ""
        item["status_rascunho_interno"] = "ORIGEM_NAO_ENCONTRADA_REVISAR"
        merged_no_phone.append(item)

    # Keep output row order stable: source/planned rows first, historical valid-phone
    # rows carried by _rows_preserving_review remain at the end, then no-phone rows.
    return merged_phone + merged_no_phone


def _worker_sheet_matches_matrix(worksheet: Any, matrix: list[list[Any]]) -> bool:
    """Return True only when a worksheet contains the expected rectangular matrix."""
    if not matrix or not matrix[0]:
        return False
    width = len(matrix[0])

    def normalize(rows: list[list[Any]]) -> list[list[str]] | None:
        normalized = [[str(value or "") for value in row] for row in rows]
        while normalized and not any(normalized[-1]):
            normalized.pop()
        result: list[list[str]] = []
        for row in normalized:
            if len(row) > width and any(row[width:]):
                return None
            row = row[:width]
            result.append(row + [""] * (width - len(row)))
        return result

    actual = normalize(worksheet.get_all_values())
    expected = normalize(matrix)
    return actual is not None and expected is not None and actual == expected


def _worker_rename_sheets_atomic(spreadsheet: Any, renames: list[tuple[Any, str]]) -> None:
    """Rename worksheets in one Google Sheets batchUpdate request."""
    if not renames:
        return
    requests = []
    for worksheet, new_title in renames:
        requests.append({
            "updateSheetProperties": {
                "properties": {"sheetId": int(worksheet.id), "title": new_title},
                "fields": "title",
            }
        })
    # The Sheets API validates and applies a batchUpdate atomically. This avoids
    # exposing an intermediate state where the official tab is missing.
    spreadsheet.batch_update({"requests": requests})


def _worker_worksheet_or_none(spreadsheet: Any, title: str) -> Any | None:
    try:
        return spreadsheet.worksheet(title)
    except Exception as exc:
        if exc.__class__.__name__ == "WorksheetNotFound":
            return None
        raise


def _worker_delete_sheet_if_exists(spreadsheet: Any, title: str) -> None:
    worksheet = _worker_worksheet_or_none(spreadsheet, title)
    if worksheet is not None:
        spreadsheet.del_worksheet(worksheet)


def _recover_pending_worker_write(spreadsheet: Any, output_tab: str, state: sqlite3.Connection) -> bool:
    """Recover an interrupted staged write without guessing about worksheet identity."""
    state_key = f"worker_pending_backup:{output_tab}"
    marker = _state_get(state, state_key, "")
    if not marker:
        return False

    # Backward compatibility with older state databases that stored only a backup title.
    if not isinstance(marker, dict):
        backup_title = str(marker or "").strip()
        if not backup_title:
            return False
        output_ws = _worker_worksheet_or_none(spreadsheet, output_tab)
        restored = False
        if output_ws is None:
            backup_ws = _worker_worksheet_or_none(spreadsheet, backup_title)
            if backup_ws is None:
                raise WorkerStopError(
                    f"Recuperação interrompida: a aba '{output_tab}' e seu backup "
                    f"'{backup_title}' não foram encontrados. Nenhuma aba será sobrescrita automaticamente."
                )
            _worker_rename_sheets_atomic(spreadsheet, [(backup_ws, output_tab)])
            restored = True
        _state_set(state, state_key, "")
        state.commit()
        if restored:
            log.warning("Recuperação concluída: o backup foi restaurado como '%s'.", output_tab)
        return restored

    stage_title = str(marker.get("stage_title", "") or "").strip()
    backup_title = str(marker.get("backup_title", "") or "").strip()
    try:
        stage_id = int(marker["stage_sheet_id"])
        old_exists = bool(marker["old_exists"])
        old_sheet_id = int(marker["old_sheet_id"]) if old_exists else None
    except (KeyError, TypeError, ValueError) as exc:
        raise WorkerStopError(
            f"Recuperação interrompida para '{output_tab}': o marcador local está incompleto. "
            "O estado foi preservado para inspeção manual."
        ) from exc

    if not stage_title.startswith("__KAEL_STAGE_"):
        raise WorkerStopError(
            f"Recuperação interrompida para '{output_tab}': nome de aba temporária inválido."
        )
    if old_exists and (not backup_title.startswith("__KAEL_BACKUP_") or old_sheet_id is None):
        raise WorkerStopError(
            f"Recuperação interrompida para '{output_tab}': dados do backup inválidos."
        )

    output_ws = _worker_worksheet_or_none(spreadsheet, output_tab)
    stage_ws = _worker_worksheet_or_none(spreadsheet, stage_title)
    backup_ws = (
        _worker_worksheet_or_none(spreadsheet, backup_title) if backup_title else None
    )

    def matches_id(worksheet: Any | None, expected_id: int | None) -> bool:
        return worksheet is not None and expected_id is not None and int(worksheet.id) == expected_id

    def clear_pending() -> None:
        _state_set(state, state_key, "")
        state.commit()

    def remove_stage_if_owned() -> None:
        if stage_ws is None:
            return
        if not matches_id(stage_ws, stage_id):
            raise WorkerStopError(
                f"Recuperação interrompida para '{output_tab}': a aba temporária mudou de identidade. "
                "Nenhuma aba foi apagada."
            )
        _worker_delete_sheet_if_exists(spreadsheet, stage_title)

    if old_exists:
        # The atomic rename either committed both names or committed neither.
        if output_ws is None:
            if backup_ws is None or not matches_id(backup_ws, old_sheet_id):
                raise WorkerStopError(
                    f"Recuperação interrompida: '{output_tab}' e o backup esperado "
                    f"'{backup_title}' não foram encontrados com segurança. O marcador foi preservado."
                )
            if stage_ws is not None and not matches_id(stage_ws, stage_id):
                raise WorkerStopError(
                    f"Recuperação interrompida para '{output_tab}': a aba temporária não corresponde "
                    "ao identificador registrado. Nenhuma aba foi apagada."
                )
            _worker_rename_sheets_atomic(spreadsheet, [(backup_ws, output_tab)])
            remove_stage_if_owned()
            clear_pending()
            log.warning("Recuperação concluída: o backup foi restaurado como '%s'.", output_tab)
            return True

        if matches_id(output_ws, stage_id):
            if not matches_id(backup_ws, old_sheet_id) or stage_ws is not None:
                raise WorkerStopError(
                    f"Recuperação ambígua para '{output_tab}': a promoção parece concluída, "
                    "mas a aba de backup não corresponde ao estado registrado. O marcador foi preservado."
                )
            clear_pending()
            log.warning("Promoção pendente de '%s' confirmada pela identidade das abas; backup mantido.", output_tab)
            return True

        if matches_id(output_ws, old_sheet_id) and backup_ws is None:
            # The old official worksheet is still in place; the atomic rename did not commit.
            remove_stage_if_owned()
            clear_pending()
            log.info("Recuperação concluída: a saída anterior '%s' permaneceu intacta.", output_tab)
            return False

        raise WorkerStopError(
            f"Recuperação ambígua para '{output_tab}': os identificadores das abas não correspondem "
            "a uma transição segura conhecida. O marcador e as abas foram preservados."
        )

    # There was no official output before the operation.
    if output_ws is not None:
        if matches_id(output_ws, stage_id) and stage_ws is None:
            created_tabs = set(_state_get(state, "worker_created_output_tabs", []))
            created_tabs.add(output_tab)
            _state_set(state, "worker_created_output_tabs", sorted(created_tabs))
            clear_pending()
            log.warning("Criação pendente de '%s' confirmada após interrupção.", output_tab)
            return True
        raise WorkerStopError(
            f"Recuperação ambígua para '{output_tab}': existe uma aba oficial, mas ela não corresponde "
            "à aba temporária registrada. Nenhuma aba foi apagada e o marcador foi preservado."
        )

    if stage_ws is not None and matches_id(stage_ws, stage_id):
        remove_stage_if_owned()
        clear_pending()
        log.info("Recuperação concluída: a promoção de '%s' não ocorreu; a aba temporária foi removida.", output_tab)
        return False

    raise WorkerStopError(
        f"Recuperação interrompida para '{output_tab}': não foi possível localizar a aba oficial "
        "nem a aba temporária esperada. O marcador foi preservado para inspeção manual."
    )


def _write_matrix(spreadsheet: Any, output_tab: str, planned_rows: list[dict[str, Any]], state: sqlite3.Connection, confirm_overwrite: bool) -> int:
    # Resolve any interrupted promotion before inspecting the official destination.
    _recover_pending_worker_write(spreadsheet, output_tab, state)

    try:
        output_ws = spreadsheet.worksheet(output_tab)
        exists = True
    except Exception as exc:
        if exc.__class__.__name__ != "WorksheetNotFound":
            raise
        output_ws = None
        exists = False

    created_tabs = set(_state_get(state, "worker_created_output_tabs", []))
    approved_tabs = set(_state_get(state, "worker_approved_output_tabs", []))
    if exists and output_tab not in created_tabs and output_tab not in approved_tabs and not confirm_overwrite:
        raise WorkerStopError(
            f"A aba de saída '{output_tab}' já existe. Para autorizá-la explicitamente, execute uma vez com --confirm-overwrite."
        )
    if exists and confirm_overwrite:
        approved_tabs.add(output_tab)
        _state_set(state, "worker_approved_output_tabs", sorted(approved_tabs))
        state.commit()

    old_rows: list[dict[str, str]] = []
    if exists:
        # Read the old destination before creating or writing any replacement.
        old_rows = _values_to_dicts(output_ws.get_all_values())

    merged = _merge_worker_rows(old_rows, planned_rows)
    headers = OUTPUT_HEADERS + WORKER_EXTRA_HEADERS
    matrix = _prepare_matrix(merged)
    if not matrix or len(matrix[0]) != len(headers):
        raise WorkerStopError("Matriz de saída inválida; nenhuma gravação foi realizada.")
    if any(len(row) != len(headers) for row in matrix):
        raise WorkerStopError("A matriz contém linhas com larguras diferentes; nenhuma gravação foi realizada.")

    token = hashlib.sha256(output_tab.encode("utf-8")).hexdigest()[:8]
    attempt = str(time.time_ns())
    stage_title = f"__KAEL_STAGE_{token}_{attempt}"
    backup_title = f"__KAEL_BACKUP_{token}_{attempt}"
    stage_ws = None

    try:
        stage_ws = spreadsheet.add_worksheet(
            title=stage_title,
            rows=max(2, len(matrix)),
            cols=len(headers),
        )
        # Resize and chunk writes are safe here because the official output remains untouched.
        stage_ws.resize(rows=max(2, len(matrix)), cols=len(headers))
        try:
            from gspread.utils import rowcol_to_a1
            last_col = rowcol_to_a1(1, len(headers)).rstrip("1")
        except ImportError:
            def col_label(number: int) -> str:
                result = ""
                while number:
                    number, rem = divmod(number - 1, 26)
                    result = chr(65 + rem) + result
                return result
            last_col = col_label(len(headers))

        for start in range(0, len(matrix), 250):
            chunk = matrix[start:start + 250]
            start_row = start + 1
            end_row = start + len(chunk)
            log.info(
                "Escrevendo aba temporária: intervalo=A%d:%s%d linhas=%d",
                start_row, last_col, end_row, len(chunk),
            )
            stage_ws.update(
                range_name=f"A{start_row}:{last_col}{end_row}",
                values=chunk,
                value_input_option="RAW",
            )

        if not _worker_sheet_matches_matrix(stage_ws, matrix):
            raise WorkerStopError(
                "A verificação da aba temporária falhou; a saída oficial foi preservada."
            )
    except Exception:
        # Best effort only: even if cleanup fails, the official output was not changed.
        try:
            _worker_delete_sheet_if_exists(spreadsheet, stage_title)
        except Exception:
            log.exception("Não foi possível remover a aba temporária '%s'; a saída oficial continua preservada.", stage_title)
        raise

    pending_key = f"worker_pending_backup:{output_tab}"
    # Persist worksheet identities before promotion so a hard process interruption can be
    # recovered without confusing a newly created official tab with an unrelated tab.
    _state_set(state, pending_key, {
        "version": 2,
        "backup_title": backup_title if exists else "",
        "stage_title": stage_title,
        "stage_sheet_id": int(stage_ws.id),
        "old_exists": bool(exists),
        "old_sheet_id": int(output_ws.id) if exists and output_ws is not None else None,
    })
    state.commit()

    try:
        if exists:
            current_output = spreadsheet.worksheet(output_tab)
            # Both title changes are issued in a single atomic Google Sheets batchUpdate.
            _worker_rename_sheets_atomic(
                spreadsheet,
                [(current_output, backup_title), (stage_ws, output_tab)],
            )
        else:
            _worker_rename_sheets_atomic(spreadsheet, [(stage_ws, output_tab)])
    except Exception:
        # A batch request can be committed even when its response is lost. Inspect live
        # sheet names and contents before deciding whether to retry or report failure.
        current_output = _worker_worksheet_or_none(spreadsheet, output_tab)
        current_stage = _worker_worksheet_or_none(spreadsheet, stage_title)
        current_backup = (
            _worker_worksheet_or_none(spreadsheet, backup_title) if exists else None
        )
        promotion_applied = (
            current_output is not None
            and current_stage is None
            and _worker_sheet_matches_matrix(current_output, matrix)
            and ((exists and current_backup is not None) or not exists)
        )
        if promotion_applied:
            _state_set(state, pending_key, "")
            if not exists:
                created_tabs.add(output_tab)
                _state_set(state, "worker_created_output_tabs", sorted(created_tabs))
            state.commit()
            log.warning(
                "A promoção de '%s' foi confirmada pela verificação da planilha, "
                "apesar de uma resposta de API ambígua.", output_tab,
            )
            return len(merged)

        try:
            _recover_pending_worker_write(spreadsheet, output_tab, state)
        except Exception:
            log.exception("Falha ao recuperar a promoção da aba '%s'.", output_tab)
        try:
            _worker_delete_sheet_if_exists(spreadsheet, stage_title)
        except Exception:
            log.exception("Não foi possível limpar a aba temporária '%s'.", stage_title)
        raise

    promoted_ws = spreadsheet.worksheet(output_tab)
    if not _worker_sheet_matches_matrix(promoted_ws, matrix):
        if exists:
            backup_ws = _worker_worksheet_or_none(spreadsheet, backup_title)
            if backup_ws is None:
                raise WorkerStopError(
                    f"A saída '{output_tab}' falhou na verificação final e o backup '{backup_title}' não foi encontrado."
                )
            # Put the questionable result back under its temporary name and restore the old output.
            _worker_rename_sheets_atomic(
                spreadsheet,
                [(promoted_ws, stage_title), (backup_ws, output_tab)],
            )
            _worker_delete_sheet_if_exists(spreadsheet, stage_title)
        else:
            spreadsheet.del_worksheet(promoted_ws)
        _state_set(state, pending_key, "")
        state.commit()
        raise WorkerStopError(
            f"A verificação da saída promovida '{output_tab}' falhou; a versão anterior foi preservada quando existia."
        )

    _state_set(state, pending_key, "")
    if not exists:
        created_tabs.add(output_tab)
        _state_set(state, "worker_created_output_tabs", sorted(created_tabs))
    state.commit()
    log.info(
        "Saída '%s' atualizada com %d registros; a gravação foi verificada e a origem não foi alterada.",
        output_tab, len(merged),
    )
    return len(merged)

def _one_cycle(*, source_tab: str, output_tab: str, limit: int, write: bool, state: sqlite3.Connection, confirm_overwrite: bool) -> dict[str, Any]:
    from .sheets_planner import _connect_sheet

    spreadsheet = _connect_sheet()
    values = _read_source_values(spreadsheet, source_tab, limit)
    source_hash = _fingerprint(values)
    cache_key = f"cache:internal-draft-v2:{source_tab}:{limit}"
    cached = _state_get(state, cache_key)

    if cached and cached.get("source_fingerprint") == source_hash:
        planned_rows = cached["planned_rows"]
        stats = cached["stats"]
        log.info("Origem sem alterações detectadas; reutilizando checkpoint local.")
    else:
        planned_rows, stats = _plan_consolidated_values(values)
        _state_set(state, cache_key, {
            "source_fingerprint": source_hash,
            "planned_rows": planned_rows,
            "stats": stats,
            "updated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        })
        state.commit()

    sync_key = f"synced:internal-draft-v2:{source_tab}:{output_tab}"
    last_synced = _state_get(state, sync_key)
    summary = {
        "source_tab": source_tab,
        "output_tab": output_tab,
        "source_fingerprint": source_hash,
        "source_changed_since_last_checkpoint": not (cached and cached.get("source_fingerprint") == source_hash),
        "stats": stats,
        "write_enabled": write,
        "written_rows": 0,
    }

    if write and last_synced != source_hash:
        written = _write_matrix(spreadsheet, output_tab, planned_rows, state, confirm_overwrite)
        _state_set(state, sync_key, source_hash)
        state.commit()
        summary["written_rows"] = written
    elif write:
        log.info("Saída já sincronizada com esta versão da origem; nenhuma gravação necessária.")
    else:
        log.info("Modo de simulação: nenhuma escrita foi feita no Google Sheets.")

    return summary


def run_lead_worker(*, once: bool = False, write: bool = False, source_tab: str | None = None, output_tab: str = DEFAULT_OUTPUT_TAB, interval_seconds: int = 300, limit: int = 0, state_db: Path | str = Path("data/kael_lead_worker.sqlite3"), confirm_overwrite: bool = False) -> None:
    """Run a persistent local worker. Writes require --write; no message sending occurs."""
    source_tab = (source_tab or settings.google_consolidation_source_tab).strip()
    output_tab = (output_tab or DEFAULT_OUTPUT_TAB).strip()
    state_db = Path(state_db)
    if not settings.google_sheets_id:
        raise WorkerStopError("GOOGLE_SHEETS_ID não está configurado no .env local.")
    credentials_path = Path(settings.google_creds_file).expanduser()
    if not credentials_path.is_absolute():
        credentials_path = Path.cwd() / credentials_path
    if not credentials_path.is_file():
        raise WorkerStopError("Arquivo local de credenciais do Google não encontrado; confira GOOGLE_CREDS_FILE sem compartilhar o conteúdo.")
    if not source_tab or not output_tab or source_tab == output_tab:
        raise WorkerStopError("A aba de origem e a aba de saída precisam ser diferentes e não vazias.")
    if not MIN_INTERVAL_SECONDS <= interval_seconds <= MAX_INTERVAL_SECONDS:
        raise WorkerStopError(f"--interval-seconds deve estar entre {MIN_INTERVAL_SECONDS} e {MAX_INTERVAL_SECONDS}.")
    if limit < 0 or limit > 100000:
        raise WorkerStopError("--limit deve estar entre 0 e 100000.")
    if limit and (not once or write):
        raise WorkerStopError("--limit só pode ser usado em uma execução única sem --write.")

    state_db.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_db.with_suffix(state_db.suffix + ".lock")
    with _lock_file(lock_path):
        state = _init_state(state_db)
        try:
            log.info(
                "Lead-worker iniciado; modo=%s; origem=%s; saída=%s; intervalo=%ss; envio WhatsApp desabilitado.",
                "WRITE" if write else "SIMULAÇÃO",
                source_tab,
                output_tab,
                interval_seconds,
            )
            while True:
                started = time.monotonic()
                try:
                    summary = _one_cycle(
                        source_tab=source_tab,
                        output_tab=output_tab,
                        limit=limit,
                        write=write,
                        state=state,
                        confirm_overwrite=confirm_overwrite,
                    )
                    stats = summary["stats"]
                    log.info(
                        "Ciclo concluído: origem_linhas=%s; grupos=%s; saída_planejada=%s; mensagens_prontas_autorizadas=%s; rascunhos_internos=%s; revisar_identidade=%s; revisar_autorizacao=%s; revisão_consolidação=%s; sem_telefone=%s; telefone_inválido=%s; conflitos=%s; escrita=%s",
                        stats.get("linhas_origem", 0),
                        stats.get("grupos_identidade", 0),
                        stats.get("linhas_consolidadas", 0),
                        stats.get("com_mensagem", 0),
                        stats.get("rascunhos_internos", 0),
                        stats.get("rascunhos_revisar_identidade", 0),
                        stats.get("rascunhos_revisar_autorizacao", 0),
                        stats.get("revisao_consolidacao", 0),
                        stats.get("sem_telefone", 0),
                        stats.get("telefone_invalido", 0),
                        stats.get("grupos_com_conflitos", 0),
                        summary.get("written_rows", 0),
                    )
                except WorkerStopError:
                    raise
                except Exception:
                    log.exception("Falha transitória no ciclo; estado local preservado e nova tentativa ocorrerá no próximo ciclo.")
                    if once:
                        raise
                if once:
                    break
                elapsed = time.monotonic() - started
                time.sleep(max(1, interval_seconds - int(elapsed)))
        finally:
            state.close()
