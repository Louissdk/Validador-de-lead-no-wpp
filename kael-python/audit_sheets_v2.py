from collections import Counter
import re
import unicodedata
from app.sheets_planner import _connect_sheet, _norm_header
from app.normalization import normalize_phone
PHONE_HEADERS = {
    "telefone", "telefone_whatsapp", "numero", "phone",
    "phonenumber", "phone_number", "celular", "whatsapp", "mobile",
}
NAME_HEADERS = {"nome", "name", "title", "empresa", "company", "nome_empresa"}
ADDRESS_HEADERS = {"endereco", "address", "localizacao", "location"}
def clean(value):
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()
def get_field(row, headers, choices):
    for i, header in enumerate(headers):
        if header in choices and i < len(row):
            value = str(row[i] or "").strip()
            if value:
                return value
    return ""
def read_tab(ws):
    values = ws.get_all_values()
    header_index = next(
        (i for i, row in enumerate(values)
         if any(str(v).strip() for v in row)),
        None,
    )
    if header_index is None:
        return {"rows": 0, "phones": Counter(), "keys": Counter()}
    headers = [_norm_header(v) for v in values[header_index]]
    phones = Counter()
    keys = Counter()
    count = 0
    for row in values[header_index + 1:]:
        if not any(str(v).strip() for v in row):
            continue
        count += 1
        raw_phone = get_field(row, headers, PHONE_HEADERS)
        phone = normalize_phone(raw_phone)
        name = clean(get_field(row, headers, NAME_HEADERS))
        address = clean(get_field(row, headers, ADDRESS_HEADERS))
        if phone:
            phones[phone] += 1
        # Chave de comparação aproximada; não prova identidade empresarial.
        if phone or name or address:
            keys[(name, phone, address)] += 1
    return {"rows": count, "phones": phones, "keys": keys}
spreadsheet = _connect_sheet()
tabs = {ws.title: read_tab(ws) for ws in spreadsheet.worksheets()}
def report(title):
    data = tabs.get(title)
    if data is None:
        print(f"{title}: ABA NÃO ENCONTRADA")
        return
    phone_counts = data["phones"]
    key_counts = data["keys"]
    print(f"\n{title}")
    print(f"  Linhas: {data['rows']}")
    print(f"  Ocorrências com telefone válido: {sum(phone_counts.values())}")
    print(f"  Telefones distintos: {len(phone_counts)}")
    print(f"  Ocorrências repetidas de telefone: "
          f"{sum(n - 1 for n in phone_counts.values() if n > 1)}")
    print(f"  Chaves de registro distintas: {len(key_counts)}")
    print(f"  Repetições da chave nome+telefone+endereço: "
          f"{sum(n - 1 for n in key_counts.values() if n > 1)}")
print("\n=== AUDITORIA COMPARATIVA KAEL ===")
print(f"Planilha: {spreadsheet.title}")
chunk_names = [
    title for title in tabs
    if re.fullmatch(r"leads_raw_\d+k", _norm_header(title))
]
chunk_names.sort(
    key=lambda title: int(re.search(r"(\d+)", title).group(1))
)
for name in ["leads_raw", *chunk_names,
             "leads_validados", "Cópia de leads_validados",
             "leads_sem_wpp", "Cópia de leads_sem_wpp"]:
    report(name)
print("\n=== COMPARAÇÃO: LEADS_RAW VS. 15 PARTES ===")
if "leads_raw" in tabs and chunk_names:
    raw = tabs["leads_raw"]
    chunks = Counter()
    chunk_phones = Counter()
    chunk_rows = 0
    for name in chunk_names:
        chunks.update(tabs[name]["keys"])
        chunk_phones.update(tabs[name]["phones"])
        chunk_rows += tabs[name]["rows"]
    raw_keys = raw["keys"]
    print(f"Linhas na aba principal: {raw['rows']}")
    print(f"Soma das linhas nas partes: {chunk_rows}")
    print(f"Chaves presentes na principal: {sum(raw_keys.values())}")
    print(f"Chaves presentes nas partes: {sum(chunks.values())}")
    print(f"Registros correspondentes (multiconjunto): "
          f"{sum((raw_keys & chunks).values())}")
    print(f"Registros da principal sem correspondência: "
          f"{sum((raw_keys - chunks).values())}")
    print(f"Registros das partes sem correspondência: "
          f"{sum((chunks - raw_keys).values())}")
    print(f"Telefones distintos na principal: {len(raw['phones'])}")
    print(f"Telefones distintos nas partes: {len(chunk_phones)}")
    print(f"Telefones exclusivos da principal: "
          f"{len(set(raw['phones']) - set(chunk_phones))}")
    print(f"Telefones exclusivos das partes: "
          f"{len(set(chunk_phones) - set(raw['phones']))}")
else:
    print("Não foi possível localizar a aba principal e as partes.")
print("\n=== COMPARAÇÃO: VALIDADO VS. CÓPIA ===")
pairs = [
    ("leads_validados", "Cópia de leads_validados"),
    ("leads_sem_wpp", "Cópia de leads_sem_wpp"),
]
for first, second in pairs:
    if first not in tabs or second not in tabs:
        continue
    a, b = tabs[first], tabs[second]
    print(f"\n{first} | {second}")
    print(f"Telefones em comum: "
          f"{len(set(a['phones']) & set(b['phones']))}")
    print(f"Telefones exclusivos de {first}: "
          f"{len(set(a['phones']) - set(b['phones']))}")
    print(f"Telefones exclusivos de {second}: "
          f"{len(set(b['phones']) - set(a['phones']))}")
    print(f"Chaves de registro em comum: "
          f"{len(set(a['keys']) & set(b['keys']))}")
print("\nAuditoria concluída. Nenhuma célula foi alterada.")
