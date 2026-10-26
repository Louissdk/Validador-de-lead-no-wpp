from collections import Counter, defaultdict
from app.sheets_planner import _connect_sheet, _norm_header
from app.normalization import normalize_phone
def read_tab(spreadsheet, title):
    ws = spreadsheet.worksheet(title)
    values = ws.get_all_values()
    if not values:
        return []
    headers = [_norm_header(v) for v in values[0]]
    rows = []
    for raw in values[1:]:
        if not any(str(v).strip() for v in raw):
            continue
        rows.append({
            headers[i]: raw[i] if i < len(raw) else ""
            for i in range(len(headers)) if headers[i]
        })
    return rows
def value(row, *keys):
    for key in keys:
        result = row.get(_norm_header(key), "")
        if str(result).strip():
            return str(result).strip()
    return ""
def phones(rows):
    return {
        p for row in rows
        if (p := normalize_phone(value(
            row, "numero", "telefone", "phoneNumber", "phone",
            "whatsapp", "celular"
        )))
    }
def status(row):
    v = value(row, "whatsapp_confirmado").lower()
    if v in {"true", "1", "sim", "yes", "verdadeiro"}:
        return "true"
    if v in {"false", "0", "nao", "não", "no", "falso"}:
        return "false"
    if v in {"pendente", "pending"}:
        return "pendente"
    return v or "(vazio)"
ss = _connect_sheet()
raw = read_tab(ss, "leads_raw")
valid = read_tab(ss, "leads_validados")
valid_copy = read_tab(ss, "Cópia de leads_validados")
no_wpp = read_tab(ss, "leads_sem_wpp")
no_wpp_copy = read_tab(ss, "Cópia de leads_sem_wpp")
raw_phones = phones(raw)
valid_phones = phones(valid) | phones(valid_copy)
no_wpp_phones = phones(no_wpp) | phones(no_wpp_copy)
classified = valid_phones | no_wpp_phones
print("\n=== COBERTURA DA BASE ===")
print(f"Telefones distintos na base bruta: {len(raw_phones)}")
print(f"Telefones nas abas de validados: {len(valid_phones)}")
print(f"Telefones nas abas sem WhatsApp: {len(no_wpp_phones)}")
print(f"Presentes nas duas categorias: {len(valid_phones & no_wpp_phones)}")
print(f"Classificados em pelo menos uma categoria: {len(classified)}")
print(f"Na base bruta ainda sem classificação: {len(raw_phones - classified)}")
print(f"Nas abas de classificação ausentes da base bruta: {len(classified - raw_phones)}")
print("\n=== CONFLITOS DE STATUS ENTRE AS ABAS ===")
status_by_phone = defaultdict(set)
for source, rows in [
    ("original", valid),
    ("copia", valid_copy),
]:
    counts = Counter()
    for row in rows:
        p = normalize_phone(value(
            row, "numero", "telefone", "phoneNumber", "phone"
        ))
        if not p:
            continue
        s = status(row)
        counts[s] += 1
        status_by_phone[p].add(s)
    print(f"\n{source}:")
    for s, count in counts.most_common():
        print(f"  {s}: {count}")
conflicting = sum(
    1 for statuses in status_by_phone.values()
    if len(statuses) > 1
)
print(f"\nTelefones com status divergentes entre registros: {conflicting}")
print("\nAuditoria concluída. Nenhuma célula foi alterada.")
