from collections import defaultdict
from app.config import settings
from app.sheets_planner import _connect_sheet, _norm_header
from app.normalization import normalize_phone
PHONE_HEADERS = {
    "telefone",
    "telefone_whatsapp",
    "numero",
    "phone",
    "phonenumber",
    "phone_number",
    "celular",
    "whatsapp",
    "mobile",
}
spreadsheet = _connect_sheet()
output_name = _norm_header(settings.google_output_tab)
total_rows = 0
total_phone_occurrences = 0
rows_without_phone_column = 0
phone_occurrences = defaultdict(list)
print("\nAUDITORIA DE ABAS - KAEL")
print(f"Planilha: {spreadsheet.title}")
print(f"Aba de saída excluída da origem: {settings.google_output_tab}")
print("=" * 90)
for ws in spreadsheet.worksheets():
    try:
        values = ws.get_all_values()
    except Exception as exc:
        print(f"\nERRO | {ws.title} | {type(exc).__name__}: {exc}")
        continue
    nonempty = [
        row for row in values
        if any(str(value).strip() for value in row)
    ]
    is_output = _norm_header(ws.title) == output_name
    marker = "SAÍDA - EXCLUÍDA" if is_output else "CANDIDATA A ORIGEM"
    if not nonempty:
        print(f"\n{ws.title} | {marker} | VAZIA")
        continue
    headers = [_norm_header(value) for value in nonempty[0]]
    data_rows = [
        row for row in nonempty[1:]
        if any(str(value).strip() for value in row)
    ]
    phone_idx = next(
        (i for i, header in enumerate(headers) if header in PHONE_HEADERS),
        None,
    )
    valid_phones = []
    if phone_idx is None:
        if not is_output:
            rows_without_phone_column += len(data_rows)
    else:
        for row in data_rows:
            raw_phone = row[phone_idx] if phone_idx < len(row) else ""
            normalized = normalize_phone(raw_phone)
            if normalized:
                valid_phones.append(normalized)
                if not is_output:
                    phone_occurrences[normalized].append(ws.title)
    print(f"\nAba: {ws.title}")
    print(f"Tipo: {marker}")
    print(f"Linhas preenchidas após cabeçalho: {len(data_rows)}")
    print(f"Coluna de telefone reconhecida: {phone_idx is not None}")
    print(f"Telefones normalizados: {len(valid_phones)}")
    print("Cabeçalhos:", " | ".join(str(v) for v in nonempty[0][:20]))
    if not is_output:
        total_rows += len(data_rows)
        total_phone_occurrences += len(valid_phones)
duplicate_occurrences = sum(
    len(tabs) - 1
    for tabs in phone_occurrences.values()
    if len(tabs) > 1
)
phones_in_multiple_tabs = sum(
    1 for tabs in phone_occurrences.values()
    if len(set(tabs)) > 1
)
print("\n" + "=" * 90)
print("RESUMO BRUTO - ABAS CANDIDATAS, EXCLUINDO A ABA DE SAÍDA")
print(f"Linhas preenchidas: {total_rows}")
print(f"Ocorrências com telefone normalizado: {total_phone_occurrences}")
print(f"Telefones normalizados distintos: {len(phone_occurrences)}")
print(f"Ocorrências repetidas de telefone: {duplicate_occurrences}")
print(f"Telefones presentes em mais de uma aba: {phones_in_multiple_tabs}")
print(f"Linhas em abas sem coluna de telefone reconhecida: {rows_without_phone_column}")
print("\nATENÇÃO: abas auxiliares e registros sem telefone ainda exigem revisão.")
print("Telefone repetido é apenas candidato a duplicata, não prova de empresa duplicada.")
