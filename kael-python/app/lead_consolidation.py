"""Conservative, auditable lead consolidation for Google Sheets.

The pure builder never contacts Google or writes data. The sheet runner only
writes when explicitly requested, and an existing output tab requires a
separate overwrite confirmation.
"""
from __future__ import annotations

import json
import re
import unicodedata
from collections import Counter, defaultdict
from typing import Any
from urllib.parse import urlparse

from .normalization import normalize_phone

CHUNK_SIZE = 250
ALIASES: dict[str, tuple[str, ...]] = {
    "nome": ("title", "nome", "name", "empresa", "company"),
    "endereco": ("address", "endereco", "location"),
    "telefone": ("telefone_normalizado", "phoneNumber", "telefone", "numero", "phone", "whatsapp"),
    "site": ("website", "site", "url", "dominio"),
    "email": ("email", "e_mail"),
    "segmento": ("type", "nicho", "categoria", "segmento"),
    "avaliacao": ("rating", "avaliacao"),
}
EXTRA_HEADERS = [
    "preview_id_v5",
    "grupo_nome_endereco",
    "quantidade_registros_origem",
    "linha_representada",
    "linhas_origem_revalidacao",
    "telefones_distintos_grupo",
    "ocorrencias_telefone",
    "status_consolidacao_v5",
    "campos_negocio_conflitantes",
    "variantes_segmento",
    "variantes_avaliacao",
    "variantes_site_email",
    "status_whatsapp_auditoria",
    "acao_sugerida",
    "campos_originais_com_variantes",
    "variantes_campos_originais",
]


def norm_header(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def clean(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def domain_of(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        url = raw if "://" in raw else "https://" + raw
        host = (urlparse(url).hostname or "").lower().removeprefix("www.")
        return host or clean(raw)
    except ValueError:
        return clean(raw)


def normalize_field(field: str, value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if field == "telefone":
        normalized = normalize_phone(raw)
        if normalized:
            return normalized
        # Retain a distinct fingerprint for malformed numbers so that an invalid
        # number is not silently merged with a different valid number.
        digits = re.sub(r"\D", "", raw)
        return f"INVALID:{digits}" if digits else f"INVALID:{clean(raw)}"
    if field == "site":
        return domain_of(raw)
    if field == "email":
        return raw.lower()
    if field == "avaliacao":
        try:
            return str(round(float(raw.replace(",", ".")), 2))
        except ValueError:
            return clean(raw)
    return clean(raw)


def _lookup_row(headers: list[str], row: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for index, header in enumerate(headers):
        key = norm_header(header)
        if key:
            result[key] = str(row[index] if index < len(row) else "")
    return result


def _get(data: dict[str, str], aliases: tuple[str, ...]) -> str:
    for alias in aliases:
        value = data.get(norm_header(alias), "")
        if str(value or "").strip():
            return str(value).strip()
    return ""


def _distinct_variants(members: list[dict[str, Any]], field: str) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for member in members:
        value = member["fields"].get(field, "")
        normalized = member["norm"].get(field, "")
        if not value or normalized in seen:
            continue
        seen.add(normalized)
        result.append(value)
    return result


def _raw_field_variants(members: list[dict[str, Any]], base_headers: list[str]) -> dict[str, list[str]]:
    """Preserve all distinct non-empty original values per source field."""
    variants: dict[str, list[str]] = {}
    for index, header in enumerate(base_headers):
        distinct: list[str] = []
        for member in members:
            row = member["row"]
            value = str(row[index] if index < len(row) else "").strip()
            if value and value not in distinct:
                distinct.append(value)
        if len(distinct) > 1:
            label = header or f"coluna_{index + 1}"
            variants[label] = distinct
    return variants
def build_consolidation_preview(values: list[list[Any]]) -> tuple[list[list[str]], dict[str, Any]]:
    """Build a worksheet matrix while preserving a reference to every source row.

    `values` includes the source header row. Input rows are never mutated.
    No network calls or external writes occur in this function.
    """
    if not values:
        raise ValueError("A aba de origem está vazia.")
    headers = [str(value or "").strip() for value in values[0]]
    if not any(headers):
        raise ValueError("A primeira linha não contém cabeçalhos.")
    normalized_headers = [norm_header(header) for header in headers]
    header_set = {header for header in normalized_headers if header}
    if not any(norm_header(alias) in header_set for alias in ALIASES["nome"]):
        raise ValueError("Não reconheci a coluna de nome/empresa na origem.")
    if not any(norm_header(alias) in header_set for alias in ALIASES["endereco"]):
        raise ValueError("Não reconheci a coluna de endereço na origem.")

    # Revalidation sheets append their audit fields after telefone_normalizado.
    base_width = normalized_headers.index("telefone_normalizado") if "telefone_normalizado" in normalized_headers else len(headers)
    base_headers = headers[:base_width]
    base_normalized_headers = normalized_headers[:base_width]
    alias_header_keys = {
        norm_header(alias)
        for aliases in ALIASES.values()
        for alias in aliases
    }

    records: list[dict[str, Any]] = []
    for row_number, raw_values in enumerate(values[1:], start=2):
        row = [str(value or "") for value in raw_values[:len(headers)]]
        row.extend([""] * (len(headers) - len(row)))
        if not any(value.strip() for value in row):
            continue
        data = _lookup_row(headers, row)
        fields = {field: _get(data, aliases) for field, aliases in ALIASES.items()}
        normalized = {field: normalize_field(field, value) for field, value in fields.items()}
        identity_name = normalized["nome"]
        identity_address = normalized["endereco"]
        key = ("NAME_ADDRESS", identity_name, identity_address) if identity_name and identity_address else ("REVIEW_ROW", row_number)
        completeness = sum(bool(fields[field]) for field in ALIASES)
        records.append({
            "row_number": row_number,
            "row": row,
            "fields": fields,
            "norm": normalized,
            "phone": normalized["telefone"] if not normalized["telefone"].startswith("INVALID:") else "",
            "phone_key": normalized["telefone"],
            "key": key,
            "completeness": completeness,
        })
    if not records:
        raise ValueError("A aba de origem não contém registros após o cabeçalho.")

    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        groups[record["key"]].append(record)

    phone_counts = Counter(record["phone"] for record in records if record["phone"])
    phone_identities: dict[str, set[tuple[str, str, str]]] = defaultdict(set)
    for record in records:
        phone = record["phone"]
        if phone and record["norm"]["nome"] and record["norm"]["endereco"]:
            phone_identities[phone].add((record["norm"]["nome"], record["norm"]["endereco"], record["norm"]["site"]))
    shared_phones = {phone for phone, identities in phone_identities.items() if len(identities) > 1}

    extra_headers = list(EXTRA_HEADERS)
    output_headers = headers + extra_headers
    matrix: list[list[str]] = [output_headers]
    group_status_counts: Counter[str] = Counter()
    row_status_counts: Counter[str] = Counter()
    conflicts_by_field: Counter[str] = Counter()
    grouped_excess = 0
    groups_with_conflicts = 0
    multiple_phone_groups = 0
    incomplete_source_rows = 0
    output_number = 0

    for group_number, (key, members) in enumerate(groups.items(), start=1):
        members.sort(key=lambda item: (-item["completeness"], item["row_number"]))
        group_id = f"NA-{group_number:06d}"
        distinct_phone_keys = {item["phone_key"] for item in members if item["phone_key"]}
        phone_conflict = len(distinct_phone_keys) > 1
        incomplete = key[0] == "REVIEW_ROW"
        output_members = members if phone_conflict else [members[0]]
        if phone_conflict:
            multiple_phone_groups += 1
        if incomplete:
            incomplete_source_rows += len(members)
        if not phone_conflict and not incomplete:
            grouped_excess += len(members) - 1

        conflicts: list[str] = []
        for field in ALIASES:
            variants = {item["norm"][field] for item in members if item["norm"][field]}
            if len(variants) > 1:
                conflicts.append(field)
                conflicts_by_field[field] += 1
        raw_variants = _raw_field_variants(members, base_headers)
        additional_conflicts = [
            field
            for field in raw_variants
            if norm_header(field) not in alias_header_keys
            and norm_header(field) != "position"
        ]
        if conflicts or additional_conflicts:
            groups_with_conflicts += 1
        for field in additional_conflicts:
            conflicts_by_field[field] += 1
        if phone_conflict:
            group_status = "REVISAR_MULTIPLOS_TELEFONES"
            action = "Manter os registros separados e confirmar se pertencem ao mesmo estabelecimento"
        elif incomplete:
            group_status = "IDENTIDADE_INCOMPLETA_REVISAR"
            action = "Manter individual; completar nome ou endereço"
        elif "nome" in conflicts or "endereco" in conflicts:
            group_status = "REVISAR_IDENTIDADE"
            action = "Revisar a identidade antes de consolidar"
        # Category and rating variants remain in the audit metadata, but by themselves
        # they do not establish an identity conflict. Site/email differences and other
        # non-alias source-field variants still require conservative review.
        elif ({"site", "email"} & set(conflicts)) or additional_conflicts:
            group_status = "CANDIDATO_COM_CONFLITOS"
            action = "Revisar as variantes comerciais antes de confirmar"
        elif len(members) > 1:
            group_status = "DUPLICATA_CANDIDATA"
            action = "Comparar as fontes antes de confirmar a consolidação"
        else:
            group_status = "SEM_DUPLICATA_EXATA_IDENTIFICADA"
            action = "Manter; a chave nome + endereço não garante unicidade real"
        group_status_counts[group_status] += 1

        all_source_rows = ",".join(str(member["row_number"]) for member in sorted(members, key=lambda item: item["row_number"]))
        variant_segment = " / ".join(_distinct_variants(members, "segmento"))
        variant_rating = " / ".join(_distinct_variants(members, "avaliacao"))
        site_email_variants = list(dict.fromkeys(
            value
            for member in members
            for value in (
                f"site={member['fields']['site']}" if member["fields"]["site"] else "",
                f"email={member['fields']['email']}" if member["fields"]["email"] else "",
            )
            if value
        ))

        for member in output_members:
            output_number += 1
            output_row = list(member["row"])
            # Fill an empty source field only when all known non-empty values agree.
            if not phone_conflict and not incomplete:
                for index, header in enumerate(base_normalized_headers):
                    if header == "position" or str(output_row[index]).strip():
                        continue
                    field_name = next((name for name, aliases in ALIASES.items() if header in {norm_header(alias) for alias in aliases}), None)
                    if field_name:
                        available = [other["fields"].get(field_name, "") for other in members if other["fields"].get(field_name, "")]
                        variants = {normalize_field(field_name, value) for value in available} - {""}
                        if available and len(variants) == 1:
                            output_row[index] = available[0]
                    else:
                        # Preencher somente se todos os valores conhecidos
                        # desse campo original forem iguais.
                        available = [
                            str(other["row"][index]).strip()
                            for other in members
                            if str(other["row"][index]).strip()
                        ]
                        if available and len(set(available)) == 1:
                            output_row[index] = available[0]
            row_status_counts[group_status] += 1
            represented_phone_count = len({member["phone_key"] for member in members if member["phone_key"]})
            output_row.extend([
                f"PREVIEW5-{output_number:06d}",
                group_id,
                str(len(members)),
                str(member["row_number"]),
                all_source_rows,
                str(represented_phone_count),
                str(phone_counts.get(member["phone"], 0) if member["phone"] else 0),
                group_status,
                ", ".join(conflicts),
                variant_segment,
                variant_rating,
                " | ".join(site_email_variants),
                "NAO_VERIFICADO",
                action,
                ", ".join(raw_variants.keys()),
                json.dumps(raw_variants, ensure_ascii=False, separators=(",", ":")) if raw_variants else "",
            ])
            matrix.append(output_row)

    # Multi-phone groups intentionally appear as separate output rows; the source
    # row reference list is repeated for review. Source coverage is measured by
    # the union of all referenced source-row numbers, not by summing qty values.
    referenced_rows: set[int] = set()
    for row in matrix[1:]:
        refs = str(row[len(headers) + 4]).split(",")
        referenced_rows.update(int(ref) for ref in refs if ref.strip().isdigit())
    stats: dict[str, Any] = {
        "linhas_origem": len(records),
        "grupos_identidade": len(groups),
        "linhas_previa": len(matrix) - 1,
        "ocorrencias_redundantes_em_grupos_sem_conflito_de_telefone": grouped_excess,
        "grupos_com_multiplos_telefones": multiple_phone_groups,
        "linhas_origem_com_nome_ou_endereco_incompleto": incomplete_source_rows,
        "grupos_com_conflitos": groups_with_conflicts,
        "telefones_compartilhados_distintos": len(shared_phones),
        "linhas_origem_referenciadas": len(referenced_rows),
        "status_por_grupo": dict(sorted(group_status_counts.items())),
        "status_por_linha_previa": dict(sorted(row_status_counts.items())),
        "conflitos_por_campo": dict(sorted(conflicts_by_field.items())),
        "campos_originais": len(headers),
        "colunas_saida": len(output_headers),
    }
    expected_source_rows = {record["row_number"] for record in records}
    if referenced_rows != expected_source_rows:
        raise AssertionError("A prévia não referencia exatamente o conjunto de linhas de origem.")
    return matrix, stats


def _write_matrix(output_ws: Any, matrix: list[list[str]]) -> None:
    from gspread.utils import rowcol_to_a1

    needed_rows = max(2, len(matrix))
    needed_cols = len(matrix[0])
    # Expand first so writing is possible. Shrinking waits until all data chunks
    # succeed, reducing risk of truncating an older preview on a write failure.
    output_ws.resize(rows=max(getattr(output_ws, "row_count", needed_rows), needed_rows),
                     cols=max(getattr(output_ws, "col_count", needed_cols), needed_cols))
    for start in range(0, len(matrix), CHUNK_SIZE):
        chunk = matrix[start:start + CHUNK_SIZE]
        start_row = start + 1
        end_row = start + len(chunk)
        output_ws.update(
            range_name=f"A{start_row}:{rowcol_to_a1(end_row, needed_cols)}",
            values=chunk,
            value_input_option="RAW",
        )
    output_ws.resize(rows=needed_rows, cols=needed_cols)
    try:
        output_ws.freeze(rows=1)
        output_ws.set_basic_filter()
        output_ws.format(
            f"A1:{rowcol_to_a1(1, needed_cols)}",
            {
                "backgroundColor": {"red": 0.05, "green": 0.13, "blue": 0.25},
                "textFormat": {"foregroundColor": {"red": 1, "green": 1, "blue": 1}, "bold": True},
                "wrapStrategy": "WRAP",
                "verticalAlignment": "MIDDLE",
            },
        )
    except Exception as exc:  # The data may be complete even if formatting fails.
        print(f"Aviso: dados gravados, mas uma parte da formatação falhou: {exc}")


def run_consolidation_sheet(
    *,
    write: bool = False,
    confirm_overwrite: bool = False,
    source_tab: str | None = None,
    output_tab: str | None = None,
    limit: int = 0,
) -> dict[str, Any]:
    """Create a conservative preview from a source tab; write only on explicit request."""
    from .config import settings
    from .sheets_planner import _connect_sheet

    source_tab = (source_tab or settings.google_consolidation_source_tab).strip()
    output_tab = (output_tab or settings.google_consolidation_output_tab).strip()
    if not source_tab or not output_tab or source_tab == output_tab:
        raise ValueError("Aba de origem e de saída precisam ser diferentes e não vazias.")
    if limit < 0 or limit > 100000:
        raise ValueError("--limit deve ser 0 ou um número entre 1 e 100000.")
    if write and limit:
        raise ValueError("Não é permitido gravar uma prévia limitada; remova --limit para gravar a consolidação completa.")

    spreadsheet = _connect_sheet()
    from gspread.exceptions import WorksheetNotFound
    try:
        source_ws = spreadsheet.worksheet(source_tab)
    except WorksheetNotFound as exc:
        raise RuntimeError(f"Não encontrei a aba de origem '{source_tab}'.") from exc
    if limit:
        # Header + requested sample only. get_all_values() would download the
        # entire source tab even when --limit 100 was passed.
        from gspread.utils import rowcol_to_a1
        headers = source_ws.row_values(1)
        if not headers:
            source_values = []
        else:
            last_col = rowcol_to_a1(1, len(headers)).rstrip("1")
            last_row = max(1, min(limit + 1, int(source_ws.row_count)))
            source_values = source_ws.get(f"A1:{last_col}{last_row}")
    else:
        source_values = source_ws.get_all_values()
    matrix, stats = build_consolidation_preview(source_values)

    existing_ws = None
    try:
        existing_ws = spreadsheet.worksheet(output_tab)
    except WorksheetNotFound:
        # Only a confirmed missing tab is treated as absent. Quota, timeout and
        # permission failures must stop here instead of triggering a create.
        existing_ws = None

    print(f"Planilha: {spreadsheet.title}")
    print(f"Origem: {source_tab} | saída: {output_tab}")
    print("Prévia: nenhuma alteração foi gravada." if not write else "Modo gravação explícita: apenas a aba de saída será criada/atualizada; a origem não será modificada.")
    for key, value in stats.items():
        if isinstance(value, dict):
            print(f"{key}:")
            for status, count in value.items():
                print(f"  {status}: {count}")
        else:
            print(f"{key}: {value}")

    if not write:
        print("Para gravar uma aba nova, repita com --write. Para atualizar uma aba de saída existente, use também --confirm-overwrite.")
        return stats
    if existing_ws is not None and not confirm_overwrite:
        raise RuntimeError(
            f"A aba de saída '{output_tab}' já existe. Nada foi alterado. "
            "Para sobrescrever somente essa aba, execute novamente com --write --confirm-overwrite."
        )
    if existing_ws is None:
        existing_ws = spreadsheet.add_worksheet(
            title=output_tab,
            rows=max(100, len(matrix)),
            cols=len(matrix[0]),
        )
    try:
        _write_matrix(existing_ws, matrix)
    except Exception:
        print(f"Falha na gravação. A aba '{output_tab}' pode conter dados parciais; a origem permanece intacta.")
        raise
    print(f"Gravação concluída: {len(matrix) - 1} linha(s) na aba '{output_tab}'. Fonte preservada.")
    return stats
