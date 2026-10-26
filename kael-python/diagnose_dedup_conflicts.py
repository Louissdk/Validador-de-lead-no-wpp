from collections import Counter, defaultdict
import re
import unicodedata
from urllib.parse import urlparse
from app.sheets_planner import _connect_sheet, _norm_header
from app.normalization import normalize_phone
TAB = "kael_deduplicacao_auditoria"
def clean(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
def normalize_value(key, value):
    value = str(value or "").strip()
    if not value:
        return ""
    if key == "phone":
        return normalize_phone(value)
    if key == "website":
        try:
            url = value if "://" in value else "https://" + value
            return (urlparse(url).hostname or "").lower().removeprefix("www.")
        except ValueError:
            return clean(value)
    if key == "email":
        return value.lower()
    if key == "rating":
        try:
            return str(round(float(value.replace(",", ".")), 2))
        except ValueError:
            return clean(value)
    return clean(value)
ss = _connect_sheet()
ws = ss.worksheet(TAB)
values = ws.get_all_values()
if not values:
    raise RuntimeError(f"A aba {TAB!r} está vazia.")
headers = [_norm_header(v) for v in values[0]]
index = {name: i for i, name in enumerate(headers) if name}
if "grupo_dedupe" not in index:
    raise RuntimeError("Coluna grupo_dedupe não encontrada.")
fields = {
    "nome": ["title", "nome", "name", "empresa", "company"],
    "endereco": ["address", "endereco", "location"],
    "telefone": ["telefone_normalizado", "phoneNumber", "telefone", "numero"],
    "site": ["website", "site", "url", "dominio"],
    "email": ["email", "e_mail"],
    "segmento": ["type", "nicho", "categoria", "segmento"],
    "avaliacao": ["rating", "avaliacao"],
}
def get(row, aliases):
    for alias in aliases:
        i = index.get(_norm_header(alias))
        if i is not None and i < len(row) and str(row[i]).strip():
            return row[i]
    return ""
groups = defaultdict(list)
ungrouped = 0
for row in values[1:]:
    if not any(str(v).strip() for v in row):
        continue
    key = get(row, ["grupo_dedupe"])
    if not key:
        ungrouped += 1
        continue
    groups[key].append(row)
repeated_groups = {
    key: rows for key, rows in groups.items() if len(rows) > 1
}
size_distribution = Counter(
    "2 registros" if len(rows) == 2
    else "3 registros" if len(rows) == 3
    else "4 ou mais registros"
    for rows in repeated_groups.values()
)
field_conflicts = Counter()
groups_with_any_conflict = 0
position_conflicts = 0
phone_conflicts = 0
details = Counter()
for rows in repeated_groups.values():
    conflicts_this_group = 0
    for field_name, aliases in fields.items():
        values_normalized = {
            normalize_value(field_name, get(row, aliases))
            for row in rows
        }
        values_normalized.discard("")
        if len(values_normalized) > 1:
            field_conflicts[field_name] += 1
            conflicts_this_group += 1
    positions = {
        str(get(row, ["position"])).strip()
        for row in rows
        if str(get(row, ["position"])).strip()
    }
    if len(positions) > 1:
        position_conflicts += 1
    phones = {
        normalize_phone(get(row, fields["telefone"]))
        for row in rows
    }
    phones.discard("")
    if len(phones) > 1:
        phone_conflicts += 1
    if conflicts_this_group:
        groups_with_any_conflict += 1
print("\n=== DIAGNÓSTICO DE CONFLITOS ===")
print(f"Linhas analisadas: {sum(len(r) for r in groups.values()) + ungrouped}")
print(f"Grupos de identidade: {len(groups)}")
print(f"Grupos repetidos: {len(repeated_groups)}")
print(f"Linhas sem grupo de identidade: {ungrouped}")
print(f"Grupos repetidos com diferenças em campos: {groups_with_any_conflict}")
print(f"Grupos com posições diferentes na fonte: {position_conflicts}")
print(f"Grupos com telefones diferentes: {phone_conflicts}")
print("\nDistribuição dos grupos repetidos:")
for label, count in sorted(size_distribution.items()):
    print(f"  {label}: {count}")
print("\nGrupos com divergência por campo:")
for field_name, count in field_conflicts.most_common():
    print(f"  {field_name}: {count}")
print("\nSomente leitura: nenhuma célula foi alterada.")
