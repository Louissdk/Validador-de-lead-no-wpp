from collections import Counter, defaultdict
import re
import unicodedata
from urllib.parse import urlparse
from app.sheets_planner import _connect_sheet, _norm_header
from app.normalization import normalize_phone
def norm(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
def domain(value):
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        url = value if "://" in value else "https://" + value
        return (urlparse(url).hostname or "").lower().removeprefix("www.")
    except ValueError:
        return norm(value)
ss = _connect_sheet()
ws = ss.worksheet("leads_raw")
values = ws.get_all_values()
if not values:
    raise RuntimeError("leads_raw está vazia.")
headers = [_norm_header(v) for v in values[0]]
idx = {name: i for i, name in enumerate(headers) if name}
for required in ("title", "address", "phonenumber"):
    if required not in idx:
        raise RuntimeError(f"Coluna necessária ausente: {required}")
def get(row, key):
    i = idx.get(key)
    return row[i] if i is not None and i < len(row) else ""
groups = defaultdict(list)
rows_read = 0
missing_identity = 0
for row in values[1:]:
    if not any(str(v).strip() for v in row):
        continue
    rows_read += 1
    name = norm(get(row, "title"))
    address = norm(get(row, "address"))
    if not name or not address:
        missing_identity += 1
        continue
    phone = normalize_phone(get(row, "phonenumber"))
    website = domain(get(row, "website"))
    segment = norm(get(row, "type"))
    groups[(name, address)].append({
        "phone": phone,
        "website": website,
        "segment": segment,
    })
repeated = {
    key: records for key, records in groups.items()
    if len(records) > 1
}
phone_variants = {}
site_variants = {}
segment_variants = {}
current_identity_variants = {}
for key, records in repeated.items():
    phones = {r["phone"] for r in records if r["phone"]}
    sites = {r["website"] for r in records if r["website"]}
    segments = {r["segment"] for r in records if r["segment"]}
    # Conta quantas combinações nome+telefone+endereço
    # existem dentro do mesmo nome+endereço.
    identities = {
        (r["phone"], r["website"])
        for r in records
        if r["phone"] or r["website"]
    }
    if len(phones) > 1:
        phone_variants[key] = len(phones)
    if len(sites) > 1:
        site_variants[key] = len(sites)
    if len(segments) > 1:
        segment_variants[key] = len(segments)
    if len(identities) > 1:
        current_identity_variants[key] = len(identities)
print("\n=== AUDITORIA DE IDENTIDADE POR NOME + ENDEREÇO ===")
print(f"Linhas brutas analisadas: {rows_read}")
print(f"Grupos exatos de nome + endereço: {len(groups)}")
print(f"Grupos com múltiplas ocorrências: {len(repeated)}")
print(f"Linhas dentro de grupos repetidos: "
      f"{sum(len(v) for v in repeated.values())}")
print(f"Excedentes se cada grupo fosse uma empresa: "
      f"{sum(len(v) - 1 for v in repeated.values())}")
print(f"Linhas sem nome ou endereço: {missing_identity}")
print("\n=== POSSÍVEIS VARIAÇÕES DE CADASTRO ===")
print(f"Grupos com telefones diferentes: {len(phone_variants)}")
print(f"Grupos com sites diferentes: {len(site_variants)}")
print(f"Grupos com segmentos diferentes: {len(segment_variants)}")
print(f"Grupos com mais de uma combinação de telefone/site: "
      f"{len(current_identity_variants)}")
print("\nSomente leitura: nenhuma célula foi alterada.")
print("Nome + endereço é um candidato de identidade, não prova definitiva.")
