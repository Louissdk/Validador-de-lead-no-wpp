from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = ROOT / "payload"
WORKER = ROOT / "app" / "lead_worker.py"
TESTS = ROOT / "tests" / "test_lead_worker.py"
NEW_WORKER = PAYLOAD / "app" / "lead_worker.py"
NEW_TESTS = PAYLOAD / "tests" / "test_lead_worker.py"

for path in (WORKER, TESTS, NEW_WORKER, NEW_TESTS):
    if not path.is_file():
        raise SystemExit(f"Arquivo necessário não encontrado: {path}. Nada foi alterado.")

worker = WORKER.read_text(encoding="utf-8")
tests = TESTS.read_text(encoding="utf-8")

if '"rascunho_comercial_interno"' in worker or "test_worker_creates_internal_draft_when_authorization_unknown" in tests:
    raise SystemExit("O patch de rascunhos internos já parece aplicado. Nada foi alterado.")

required_worker_markers = (
    "def _plan_consolidated_values(",
    'cache_key = f"cache:{source_tab}:{limit}"',
    'sync_key = f"synced:{source_tab}:{output_tab}"',
    "def run_lead_worker(",
)
if not all(marker in worker for marker in required_worker_markers):
    raise SystemExit(
        "O worker local difere da versão esperada. Nenhum arquivo foi alterado; faça uma inspeção manual."
    )
# Local regression suite may import _plan_consolidated_values instead of defining it.
# Validate stable test cases that prove this is the expected base worker suite.
required_test_markers = (
    "def test_worker_only_prepares_message_for_unambiguous_documented_opt_in",
    "def test_one_cycle_caches_local_plan_and_does_not_touch_output_in_simulation",
    "def test_existing_worker_output_requires_explicit_confirmation",
)
if not all(marker in tests for marker in required_test_markers):
    raise SystemExit(
        "Os testes locais não correspondem à base esperada. Nada foi alterado."
    )

replacement_worker = NEW_WORKER.read_text(encoding="utf-8")
replacement_tests = NEW_TESTS.read_text(encoding="utf-8")
compile(replacement_worker, str(WORKER), "exec")
compile(replacement_tests, str(TESTS), "exec")

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backups = [
    WORKER.with_name(WORKER.name + f".pre_internal_drafts_{stamp}.bak"),
    TESTS.with_name(TESTS.name + f".pre_internal_drafts_{stamp}.bak"),
]
temps = [
    WORKER.with_name(WORKER.name + ".internal_drafts.tmp"),
    TESTS.with_name(TESTS.name + ".internal_drafts.tmp"),
]
if any(path.exists() for path in backups + temps):
    raise SystemExit("Já existe backup ou arquivo temporário com esse nome. Nada foi alterado.")

shutil.copy2(WORKER, backups[0])
shutil.copy2(TESTS, backups[1])
try:
    temps[0].write_text(replacement_worker, encoding="utf-8")
    temps[1].write_text(replacement_tests, encoding="utf-8")
    temps[0].replace(WORKER)
    temps[1].replace(TESTS)
except Exception:
    shutil.copy2(backups[0], WORKER)
    shutil.copy2(backups[1], TESTS)
    for temp in temps:
        if temp.exists():
            temp.unlink()
    raise

print("Patch de rascunhos internos aplicado com sucesso.")
print(f"Módulo atualizado: {WORKER.relative_to(ROOT)}")
print(f"Testes atualizados: {TESTS.relative_to(ROOT)}")
print("Backups preservados:")
for path in backups:
    print(f"  {path.relative_to(ROOT)}")
print("Cache e checkpoint de sincronização foram versionados para incluir os novos campos.")
print("O .env e credentials.json não foram lidos nem alterados.")
print("Nenhuma conexão com o Google Sheets foi realizada pelo instalador.")
