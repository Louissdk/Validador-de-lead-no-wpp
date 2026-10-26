from collections import Counter
import phonenumbers
from phonenumbers import NumberParseException
from app.sheets_planner import _connect_sheet, _norm_header
from app.normalization import normalize_phone
PHONE_HEADERS = {
    "telefone", "numero", "phone", "phonenumber",
    "phone_number", "celular", "whatsapp", "mobile",
}
spreadsheet = _connect_sheet()
ws = spreadsheet.worksheet("leads_raw")
values = ws.get_all_values()
if not values:
    raise RuntimeError("A aba leads_raw está vazia.")
headers = [_norm_header(v) for v in values[0]]
phone_index = next(
    (i for i, header in enumerate(headers) if header in PHONE_HEADERS),
    None,
)
if phone_index is None:
    raise RuntimeError("Coluna de telefone não encontrada.")
row_counts = Counter()
unique_phone_status = {}
total_rows = 0
def classify(raw):
    if not str(raw or "").strip():
        return "SEM_TELEFONE", None
    normalized = normalize_phone(raw)
    if not normalized:
        return "FORMATO_INVALIDO", None
    if normalized in unique_phone_status:
        return unique_phone_status[normalized], normalized
    try:
        number = phonenumbers.parse("+" + normalized, None)
    except NumberParseException:
        result = "FORMATO_INVALIDO"
    else:
        if not phonenumbers.is_possible_number(number):
            result = "NUMERO_IMPOSSIVEL"
        elif not phonenumbers.is_valid_number(number):
            result = "POSSIVEL_NAO_VALIDO"
        else:
            result = "VALIDO_ESTRUTURAL"
    unique_phone_status[normalized] = result
    return result, normalized
for row in values[1:]:
    if not any(str(v).strip() for v in row):
        continue
    total_rows += 1
    raw = row[phone_index] if phone_index < len(row) else ""
    result, _ = classify(raw)
    row_counts[result] += 1
unique_counts = Counter(unique_phone_status.values())
print("\n=== RELATORIO DE QUALIDADE TELEFONICA ===")
print(f"Planilha: {spreadsheet.title}")
print(f"Aba analisada: {ws.title}")
print(f"Registros brutos: {total_rows}")
print(f"Telefones distintos normalizáveis: {len(unique_phone_status)}")
print("\nRESULTADOS POR REGISTRO")
for status, count in sorted(row_counts.items()):
    print(f"{status}: {count}")
print("\nRESULTADOS POR TELEFONE DISTINTO")
for status, count in sorted(unique_counts.items()):
    print(f"{status}: {count}")
print("\nAuditoria concluída. Nenhuma célula foi alterada.")
print("VALIDO_ESTRUTURAL não significa WhatsApp ativo ou consentimento.")
