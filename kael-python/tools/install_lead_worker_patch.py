from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "app" / "cli.py"
GITIGNORE = ROOT / ".gitignore"
MODULE = ROOT / "app" / "lead_worker.py"
TEST = ROOT / "tests" / "test_lead_worker.py"

if not all(path.is_file() for path in (CLI, GITIGNORE, MODULE, TEST)):
    raise SystemExit(
        "Patch incompleto. Extraia o pacote na raiz do projeto, mantendo as pastas app, tests, tools e docs."
    )

cli_text = CLI.read_text(encoding="utf-8")
gitignore_text = GITIGNORE.read_text(encoding="utf-8")

if 'lead_worker = sub.add_parser("lead-worker"' in cli_text:
    raise SystemExit("O comando lead-worker já está registrado na CLI. Nenhuma alteração foi feita.")

parser_anchor = (
    '    consolidate.add_argument("--confirm-overwrite", action="store_true", '
    'help="Confirma sobrescrever a aba de saída caso ela já exista")\n'
    '    args = parser.parse_args()\n'
)
parser_replacement = (
    '    consolidate.add_argument("--confirm-overwrite", action="store_true", '
    'help="Confirma sobrescrever a aba de saída caso ela já exista")\n'
    '    lead_worker = sub.add_parser("lead-worker", help="Validar leads continuamente e preparar rascunhos; nunca envia WhatsApp")\n'
    '    lead_worker.add_argument("--once", action="store_true", help="Executa apenas um ciclo e encerra")\n'
    '    lead_worker.add_argument("--write", action="store_true", help="Autoriza atualizar a aba de planejamento; sem isso é simulação")\n'
    '    lead_worker.add_argument("--source-tab", default=None, help="Aba bruta de origem; padrão GOOGLE_CONSOLIDATION_SOURCE_TAB")\n'
    '    lead_worker.add_argument("--output-tab", default="kael_planejamento_continuo", help="Aba gerenciada de saída; padrão kael_planejamento_continuo")\n'
    '    lead_worker.add_argument("--interval-seconds", type=int, default=300, help="Intervalo do worker (60 a 86400 segundos; padrão 300)")\n'
    '    lead_worker.add_argument("--limit", type=int, default=0, help="Amostra limitada só para uma execução de simulação")\n'
    '    lead_worker.add_argument("--state-db", type=Path, default=Path("data/kael_lead_worker.sqlite3"), help="Arquivo SQLite local para checkpoints")\n'
    '    lead_worker.add_argument("--confirm-overwrite", action="store_true", help="Autoriza usar a aba de saída se ela já existir")\n'
    '    args = parser.parse_args()\n'
)
if cli_text.count(parser_anchor) != 1:
    raise SystemExit(
        "Não encontrei exatamente o ponto de inclusão esperado na CLI atual. "
        "Nenhum arquivo existente foi alterado. Compare app/cli.py com a versão atual do KAEL."
    )

branch_anchor = '        elif args.command == "worker": run_worker(args.once)\n        elif args.command == "serve":\n'
branch_replacement = '''        elif args.command == "worker": run_worker(args.once)
        elif args.command == "lead-worker":
            from .lead_worker import run_lead_worker
            run_lead_worker(
                once=args.once,
                write=args.write,
                source_tab=args.source_tab,
                output_tab=args.output_tab,
                interval_seconds=args.interval_seconds,
                limit=args.limit,
                state_db=args.state_db,
                confirm_overwrite=args.confirm_overwrite,
            )
        elif args.command == "serve":
'''
if cli_text.count(branch_anchor) != 1:
    raise SystemExit(
        "Não encontrei exatamente o ponto de despacho esperado na CLI. "
        "Nenhum arquivo existente foi alterado. Compare app/cli.py com a versão atual do KAEL."
    )

updated_cli = cli_text.replace(parser_anchor, parser_replacement, 1).replace(branch_anchor, branch_replacement, 1)
compile(updated_cli, str(CLI), "exec")

ignore_lines = [
    "# Local KAEL lead-worker state and single-instance lock",
    "data/kael_lead_worker.sqlite3",
    "data/kael_lead_worker.sqlite3-shm",
    "data/kael_lead_worker.sqlite3-wal",
    "data/kael_lead_worker.sqlite3.lock",
]
updated_gitignore = gitignore_text.rstrip()
for line in ignore_lines:
    if line not in updated_gitignore.splitlines():
        updated_gitignore += "\n" + line
updated_gitignore += "\n"

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
backups = [
    CLI.with_name(CLI.name + f".pre_lead_worker_{stamp}.bak"),
    GITIGNORE.with_name(GITIGNORE.name + f".pre_lead_worker_{stamp}.bak"),
]
temps = [
    CLI.with_name(CLI.name + ".lead_worker.tmp"),
    GITIGNORE.with_name(GITIGNORE.name + ".lead_worker.tmp"),
]
if any(path.exists() for path in backups + temps):
    raise SystemExit("Já existem backups ou arquivos temporários com o mesmo nome. Nenhuma alteração foi feita.")

shutil.copy2(CLI, backups[0])
shutil.copy2(GITIGNORE, backups[1])
try:
    temps[0].write_text(updated_cli, encoding="utf-8")
    temps[1].write_text(updated_gitignore, encoding="utf-8")
    temps[0].replace(CLI)
    temps[1].replace(GITIGNORE)
except Exception:
    shutil.copy2(backups[0], CLI)
    shutil.copy2(backups[1], GITIGNORE)
    for temp in temps:
        if temp.exists():
            temp.unlink()
    raise

print("Patch do lead-worker aplicado.")
print(f"Módulo: {MODULE.relative_to(ROOT)}")
print(f"Testes: {TEST.relative_to(ROOT)}")
print("Backups preservados:")
for path in backups:
    print(f"  {path.relative_to(ROOT)}")
print("O .env e credentials.json não foram lidos nem alterados.")
print("Nenhuma conexão com o Google Sheets foi realizada pelo instalador.")
