from collections import Counter, defaultdict
import unicodedata
from app.sheets_planner import _connect_sheet, _norm_header
from app.normalization import normalize_phone
ALIASES = {
    "phone": {"telefone", "numero", "phone", "phonenumber",
              "phone_number", "celular", "whatsapp", "mobile"},
    "name": {"nome", "name", "title", "empresa", "company", "nome_empresa"},
    "address": {"endereco", "address", "localizacao", "location"},
    "website": {"website", "site", "url", "dominio"},
    "type": {"type", "nicho", "categoria", "segmento"},
    "whatsapp_status": {"whatsapp_confirmado"},
}
def clean(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(c for c in text if not unicodedata.combining(c)).lower().strip()
def read_tab(ws):
    values = ws.get_all_values()
    first = next(
        (i for i, row in enumerate(values)
         if any(str(v).strip() for v in row)),
        None,
    )
    if first is None:
        return []
    headers = [_norm_header(v) for v in values[first]]
    result = []
    for row in values[first + 1:]:
        if not any(str(v).strip() for v in row):
            continue
        record = {}
        for i, header in enumerate(headers):
            if header and i < len(row):
                record[header] = row[i]
        result.append(record)
    return result
def field(row, kind):
    for key in ALIASES[kind]:
        value = row.get(key, "")
        if str(value or "").strip():
            return str(value).strip()
    return ""
def phone_set(rows):
    return {
        phone for row in rows
        if (phone := normalize_phone(field(row, "phone")))
    }
spreadsheet = _connect_sheet()
names = [
    "leads_raw",
    "leads_validados",
    "Cópia de leads_validados",
    "leads_sem_wpp",
    "Cópia de leads_sem_wpp",
]
data = {}
for name in names:
    try:
        data[name] = read_tab(spreadsheet.worksheet(name))
    except Exception:
        print(f"AVISO: não foi possível ler a aba {name!r}")
        data[name] = []
sets = {name: phone_set(rows) for name, rows in data.items()}
print("\n=== COBERTURA ENTRE AS FONTES ===")
print(f"Planilha: {spreadsheet.title}")
for name in names:
    rows = data[name]
    phones = sets[name]
    print(f"\n{name}")
    print(f"  Linhas: {len(rows)}")
    print(f"  Telefones distintos normalizáveis: {len(phones)}")
    if name in {"leads_validados", "Cópia de leads_validados"}:
        statuses = Counter(
            (field(row, "whatsapp_status") or "(vazio)").lower()
            for row in rows
        )
        print("  Valores de whatsapp_confirmado:")
        for status, count in statuses.most_common():
            print(f"    {status}: {count}")
raw = sets["leads_raw"]
print("\n=== COBERTURA DA BASE BRUTA ===")
for name in names[1:]:
    current = sets[name]
    overlap = raw & current
    print(f"\n{name} versus leads_raw")
    print(f"  Telefones em comum: {len(overlap)}")
    print(f"  Telefones da fonte ausentes na base bruta: "
          f"{len(current - raw)}")
    print(f"  Telefones da base bruta ausentes na fonte: "
          f"{len(raw - current)}")
print("\n=== CONSOLIDAÇÃO VALIDADO + CÓPIA ===")
a = sets["leads_validados"]
b = sets["Cópia de leads_validados"]
print(f"Telefones na união das duas abas: {len(a | b)}")
print(f"Telefones em comum: {len(a & b)}")
print(f"Somente na aba original: {len(a - b)}")
print(f"Somente na cópia: {len(b - a)}")
print("\nAuditoria concluída. Nenhuma célula foi alterada.")
