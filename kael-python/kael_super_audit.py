from collections import Counter, defaultdict
from app.sheets_planner import _connect_sheet, _norm_header
from app.normalization import normalize_phone
SOURCES = {
    "raw": "leads_raw",
    "valid": "leads_validados",
    "valid_copy": "Cópia de leads_validados",
    "no_wpp": "leads_sem_wpp",
    "no_wpp_copy": "Cópia de leads_sem_wpp",
    "revalidation": "kael_revalidacao_v2",
    "dedupe": "kael_deduplicacao_auditoria",
    "preview": "kael_leads_consolidados_preview_v5",
}
def norm(value):
    return _norm_header(str(value or ""))
def read_tab(spreadsheet, title):
    ws = spreadsheet.worksheet(title)
    values = ws.get_all_values()
    if not values:
        return {"headers": [], "rows": []}
    first = next(
        (i for i, row in enumerate(values)
         if any(str(v).strip() for v in row)),
        None,
    )
    if first is None:
        return {"headers": [], "rows": []}
    headers = [norm(v) for v in values[first]]
    rows = []
    for sheet_row, raw in enumerate(values[first + 1:], start=first + 2):
        if not any(str(v).strip() for v in raw):
            continue
        data = {
            headers[i]: raw[i] if i < len(raw) else ""
            for i in range(len(headers)) if headers[i]
        }
        rows.append({"sheet_row": sheet_row, "data": data})
    return {"headers": headers, "rows": rows}
def get(data, *aliases):
    for alias in aliases:
        value = data.get(norm(alias), "")
        if str(value or "").strip():
            return str(value).strip()
    return ""
def phone(row):
    return normalize_phone(get(
        row,
        "telefone_normalizado", "phoneNumber", "numero",
        "telefone", "phone", "whatsapp", "celular",
    ))
def phone_set(rows):
    return {
        p for item in rows
        if (p := phone(item["data"]))
    }
ss = _connect_sheet()
available = {ws.title for ws in ss.worksheets()}
missing_tabs = [
    title for title in SOURCES.values()
    if title not in available
]
if missing_tabs:
    raise RuntimeError(f"Abas ausentes; nenhuma alteração feita: {missing_tabs}")
data = {
    key: read_tab(ss, title)
    for key, title in SOURCES.items()
}
raw_rows = data["raw"]["rows"]
raw_phones = phone_set(raw_rows)
print("\n" + "=" * 72)
print("KAEL - AUDITORIA MESTRA DE CONSISTÊNCIA")
print("=" * 72)
print("\n1. BASE BRUTA")
print(f"Linhas: {len(raw_rows)}")
print(f"Telefones distintos normalizáveis: {len(raw_phones)}")
print(f"Ocorrências com telefone normalizável: "
      f"{sum(bool(phone(item['data'])) for item in raw_rows)}")
for label, key in [
    ("Validados original", "valid"),
    ("Validados cópia", "valid_copy"),
    ("Sem WhatsApp original", "no_wpp"),
    ("Sem WhatsApp cópia", "no_wpp_copy"),
]:
    rows = data[key]["rows"]
    phones = phone_set(rows)
    print(f"{label}: {len(rows)} linhas; {len(phones)} telefones distintos")
valid_union = phone_set(data["valid"]["rows"]) | phone_set(data["valid_copy"]["rows"])
no_wpp_union = phone_set(data["no_wpp"]["rows"]) | phone_set(data["no_wpp_copy"]["rows"])
classified_union = valid_union | no_wpp_union
print("\n2. COBERTURA DOS TELEFONES")
print(f"Telefones na união das abas de validados: {len(valid_union)}")
print(f"Telefones na união das abas sem WhatsApp: {len(no_wpp_union)}")
print(f"Presentes nas duas categorias: {len(valid_union & no_wpp_union)}")
print(f"Classificados em alguma categoria: {len(classified_union)}")
print(f"Telefones brutos ainda sem categoria: {len(raw_phones - classified_union)}")
print(f"Telefones das categorias ausentes na base bruta: "
      f"{len(classified_union - raw_phones)}")
status_by_phone = defaultdict(set)
for key in ("valid", "valid_copy"):
    for item in data[key]["rows"]:
        p = phone(item["data"])
        if p:
            status = get(item["data"], "whatsapp_confirmado").lower() or "(vazio)"
            status_by_phone[p].add(status)
status_conflicts = sum(
    1 for statuses in status_by_phone.values()
    if len(statuses) > 1
)
print(f"Telefones com status divergente entre validados: {status_conflicts}")
print("\n3. QUALIDADE DA REVALIDAÇÃO ESTRUTURAL")
revalidation = data["revalidation"]["rows"]
structural_status = Counter(
    get(item["data"], "validacao_estrutural") or "(vazio)"
    for item in revalidation
)
for status, count in sorted(structural_status.items()):
    print(f"{status}: {count}")
revalidated_phones = {
    p for item in revalidation
    if (p := phone(item["data"]))
}
print(f"Linhas de revalidação: {len(revalidation)}")
print(f"Telefones normalizados na revalidação: {len(revalidated_phones)}")
print(f"Telefones brutos ausentes da revalidação: "
      f"{len(raw_phones - revalidated_phones)}")
print("\n4. CONSISTÊNCIA DA PRÉVIA V5")
preview = data["preview"]["rows"]
group_rows = defaultdict(list)
preview_ids = []
source_refs = defaultdict(set)
invalid_source_refs = 0
qty_by_group = {}
status_by_group = defaultdict(set)
source_count_by_group = {}
for item in preview:
    row = item["data"]
    preview_id = get(row, "preview_id_v5")
    group_id = get(row, "grupo_nome_endereco")
    qty_raw = get(row, "quantidade_registros_origem")
    status = get(row, "status_consolidacao_v5")
    refs_raw = get(row, "linhas_origem_revalidacao")
    if preview_id:
        preview_ids.append(preview_id)
    if group_id:
        group_rows[group_id].append(item)
        status_by_group[group_id].add(status)
        try:
            qty = int(qty_raw)
        except (ValueError, TypeError):
            qty = -1
        if group_id not in qty_by_group:
            qty_by_group[group_id] = qty
            source_count_by_group[group_id] = set()
        for token in refs_raw.split(","):
            token = token.strip()
            if not token:
                continue
            try:
                source_count_by_group[group_id].add(int(token))
            except ValueError:
                invalid_source_refs += 1
        if refs_raw:
            for token in refs_raw.split(","):
                token = token.strip()
                if not token:
                    continue
                try:
                    source_row = int(token)
                    source_refs[source_row].add(group_id)
                except ValueError:
                    pass
print(f"Linhas na prévia: {len(preview)}")
print(f"IDs de prévia não vazios: {len(preview_ids)}")
print(f"IDs de prévia repetidos: {len(preview_ids) - len(set(preview_ids))}")
print(f"Grupos distintos: {len(group_rows)}")
print(f"Grupos com status inconsistente: "
      f"{sum(len(s) > 1 for s in status_by_group.values())}")
print(f"Total de registros por grupos, somando uma vez cada grupo: "
      f"{sum(max(0, q) for q in qty_by_group.values())}")
print(f"Grupos sem uma contagem válida: "
      f"{sum(q < 0 for q in qty_by_group.values())}")
print(f"Referências de origem inválidas: {invalid_source_refs}")
expected_source_rows = set(range(2, len(revalidation) + 2))
found_source_rows = set(source_refs)
print(f"Linhas de origem distintas representadas: {len(found_source_rows)}")
print(f"Linhas de origem esperadas: {len(expected_source_rows)}")
print(f"Linhas de origem ausentes: "
      f"{len(expected_source_rows - found_source_rows)}")
print(f"Referências de origem fora do intervalo esperado: "
      f"{len(found_source_rows - expected_source_rows)}")
print(f"Linhas associadas a mais de um grupo: "
      f"{sum(len(groups) > 1 for groups in source_refs.values())}")
group_status_counts = Counter()
for group_id, statuses in status_by_group.items():
    if len(statuses) == 1:
        group_status_counts[next(iter(statuses))] += 1
    else:
        group_status_counts["STATUS_INCONSISTENTE"] += 1
print("\nStatus por grupo, não por linha:")
for status, count in sorted(group_status_counts.items()):
    print(f"{status}: {count}")
print("\n5. CONTAGEM DE DUPLICATAS NA AUDITORIA")
audit = data["dedupe"]["rows"]
audit_groups = Counter(
    get(item["data"], "grupo_dedupe")
    for item in audit
    if get(item["data"], "grupo_dedupe")
)
repeat_groups = {
    group: count for group, count in audit_groups.items()
    if count > 1
}
print(f"Linhas na auditoria: {len(audit)}")
print(f"Grupos com chave: {len(audit_groups)}")
print(f"Grupos repetidos: {len(repeat_groups)}")
print(f"Linhas em grupos repetidos: {sum(repeat_groups.values())}")
print(f"Excedentes nesses grupos: "
      f"{sum(count - 1 for count in repeat_groups.values())}")
print("\n" + "=" * 72)
print("AUDITORIA FINALIZADA - SOMENTE LEITURA")
print("Nenhuma planilha foi modificada e nenhum contato foi consultado no WhatsApp.")
print("=" * 72)
