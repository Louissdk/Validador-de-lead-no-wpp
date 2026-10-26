from collections import Counter, defaultdict
from app.sheets_planner import _connect_sheet, _norm_header
ss = _connect_sheet()
ws = ss.worksheet("kael_deduplicacao_auditoria")
values = ws.get_all_values()
headers = [_norm_header(v) for v in values[0]]
def index(name):
    try:
        return headers.index(_norm_header(name))
    except ValueError:
        raise RuntimeError(f"Coluna ausente: {name}")
cols = {
    name: index(name)
    for name in [
        "grupo_dedupe",
        "ocorrencias_identidade",
        "telefone_normalizado",
        "possiveis_empresas_por_telefone",
        "status_deduplicacao",
    ]
}
rows = [
    row for row in values[1:]
    if any(str(v).strip() for v in row)
]
def cell(row, name):
    i = cols[name]
    return row[i].strip() if i < len(row) else ""
group_counts = Counter(
    cell(row, "grupo_dedupe")
    for row in rows
    if cell(row, "grupo_dedupe")
)
status_counts = Counter(
    cell(row, "status_deduplicacao")
    for row in rows
)
phone_groups = defaultdict(set)
phone_rows = defaultdict(int)
for row in rows:
    phone = cell(row, "telefone_normalizado")
    if not phone:
        continue
    phone_rows[phone] += 1
    possibilities = cell(row, "possiveis_empresas_por_telefone")
    try:
        possibilities = int(possibilities)
    except (ValueError, TypeError):
        possibilities = 0
    if possibilities > 1:
        phone_groups[phone].add(possibilities)
repeat_groups = {
    group: count
    for group, count in group_counts.items()
    if count > 1
}
actual_duplicate_rows = sum(repeat_groups.values())
actual_excess = sum(count - 1 for count in repeat_groups.values())
single_groups = sum(1 for count in group_counts.values() if count == 1)
inconsistent_occurrences = 0
for row in rows:
    group = cell(row, "grupo_dedupe")
    if not group:
        continue
    try:
        reported = int(cell(row, "ocorrencias_identidade"))
    except (ValueError, TypeError):
        inconsistent_occurrences += 1
        continue
    if reported != group_counts[group]:
        inconsistent_occurrences += 1
print("\n=== CONFERÊNCIA DA DEDUPLICAÇÃO ===")
print(f"Linhas analisadas: {len(rows)}")
print(f"Grupos de identidade: {len(group_counts)}")
print(f"Grupos com repetição: {len(repeat_groups)}")
print(f"Linhas em grupos repetidos: {actual_duplicate_rows}")
print(f"Excedentes após uma ocorrência por grupo: {actual_excess}")
print(f"Grupos com uma única ocorrência: {single_groups}")
print(f"Registros com contagem de grupo inconsistente: {inconsistent_occurrences}")
print("\n=== TELEFONES COMPARTILHADOS ===")
print(f"Telefones associados a múltiplas identidades: {len(phone_groups)}")
print(f"Linhas afetadas por esses telefones: "
      f"{sum(phone_rows[p] for p in phone_groups)}")
print("\n=== STATUS DA AUDITORIA ===")
for status, count in sorted(status_counts.items()):
    print(f"{status}: {count}")
print("\nSomente leitura; nenhuma célula foi modificada.")
