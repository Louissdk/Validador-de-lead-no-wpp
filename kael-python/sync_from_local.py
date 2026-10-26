#!/usr/bin/env python3
"""Sincroniza o código KAEL local para esta pasta Git, sem enviar dados privados."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

DEST = Path(__file__).resolve().parent
DIRS = ("app", "tests", "docs", "tools")
ROOT_FILES = {
    ".env.example",
    "ATUALIZACAO_COTA_GOOGLE_SHEETS.md",
    "DEPLOY_EASYPANEL.md",
    "Dockerfile",
    "docker-compose.yml",
    "pytest.ini",
    "requirements.txt",
    "audit_phone_quality.py",
    "audit_sheets.py",
    "audit_sheets_v2.py",
    "audit_sheets_v3.py",
    "audit_sheets_v4.py",
    "diagnose_dedup_conflicts.py",
    "diagnose_name_address.py",
    "kael_super_audit.py",
    "verify_dedup_audit.py",
}
EXCLUDED_DIRS = {
    ".git", ".venv", "venv", "__pycache__", ".pytest_cache",
    "data", "reports", "logs", "node_modules", "dist", "build",
}
EXCLUDED_NAMES = {".env", "credentials.json", "service-account.json"}
EXCLUDED_SUFFIXES = {
    ".bak", ".pyc", ".sqlite", ".sqlite3", ".db", ".csv",
    ".xlsx", ".log", ".lock", ".zip", ".pem", ".key",
}


def is_excluded(path: Path) -> bool:
    if any(part.lower() in EXCLUDED_DIRS for part in path.parts):
        return True
    name = path.name.lower()
    if name in EXCLUDED_NAMES:
        return True
    if name.startswith(".env.") and name != ".env.example":
        return True
    if any(name.endswith(suffix) for suffix in EXCLUDED_SUFFIXES):
        return True
    if ".bak" in name or ".failed_" in name:
        return True
    if name.endswith(".json") and (
        "credential" in name or "service-account" in name
    ):
        return True
    return False


def is_forbidden_repo_path(value: str) -> bool:
    path = Path(value)
    return is_excluded(path)


def run(args: list[str], *, cwd: Path) -> None:
    result = subprocess.run(args, cwd=cwd)
    if result.returncode:
        raise RuntimeError(
            f"Comando falhou ({result.returncode}): {' '.join(args)}"
        )


def collect_source(source: Path) -> dict[str, Path]:
    files: dict[str, Path] = {}

    for folder in DIRS:
        base = source / folder
        if not base.is_dir():
            continue
        for path in base.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(source)
            if is_excluded(rel):
                continue
            if folder == "tests" and (
                path.suffix != ".py" or not path.name.startswith("test_")
            ):
                continue
            if folder == "docs" and path.suffix.lower() != ".md":
                continue
            if folder in {"app", "tools"} and path.suffix.lower() != ".py":
                continue
            files[rel.as_posix()] = path

    for filename in ROOT_FILES:
        path = source / filename
        if path.is_file() and not is_excluded(Path(filename)):
            files[filename] = path

    upstream_readme = source / "README.md"
    if upstream_readme.is_file():
        files["docs/README_UPSTREAM.md"] = upstream_readme

    return files


def remove_stale_managed_files(expected: set[str]) -> int:
    removed = 0
    for folder in DIRS:
        base = DEST / folder
        if not base.exists():
            continue
        for path in sorted(base.rglob("*"), reverse=True):
            if path.is_file():
                rel = path.relative_to(DEST).as_posix()
                if rel not in expected and path.suffix.lower() in {
                    ".py", ".md", ".yml", ".yaml", ".toml", ".ini"
                }:
                    path.unlink()
                    removed += 1
            elif path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    pass

    for filename in ROOT_FILES:
        path = DEST / filename
        if path.is_file() and filename not in expected:
            path.unlink()
            removed += 1
    upstream = DEST / "docs" / "README_UPSTREAM.md"
    if upstream.is_file() and "docs/README_UPSTREAM.md" not in expected:
        upstream.unlink()
        removed += 1
    return removed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sincroniza o robô KAEL para o diretório versionado."
    )
    parser.add_argument(
        "--source",
        default=str(
            Path.home() / "Downloads" / "robo python" / "visivel-kael-python"
        ),
        help="Caminho da pasta local visivel-kael-python.",
    )
    parser.add_argument(
        "--commit",
        action="store_true",
        help="Faz commit e push depois que todos os testes passarem.",
    )
    parser.add_argument(
        "--skip-tests",
        action="store_true",
        help="Pula os testes; não recomendado para publicação.",
    )
    args = parser.parse_args()

    source = Path(args.source).expanduser().resolve()
    if not source.is_dir():
        print(f"ERRO: pasta de origem não encontrada: {source}", file=sys.stderr)
        return 2
    if source == DEST or DEST in source.parents or source in DEST.parents:
        print("ERRO: origem e destino se sobrepõem; operação abortada.", file=sys.stderr)
        return 2
    if not (source / "app" / "lead_worker.py").is_file():
        print("ERRO: app/lead_worker.py não encontrado na origem.", file=sys.stderr)
        return 2
    if not (source / "tests" / "test_worker_staged_promotion.py").is_file():
        print("ERRO: testes de staging/promoção não encontrados na origem.", file=sys.stderr)
        return 2

    root_result = subprocess.run(
        ["git", "-C", str(DEST), "rev-parse", "--show-toplevel"],
        text=True, capture_output=True,
    )
    if root_result.returncode:
        print("ERRO: o destino precisa estar dentro de um clone Git.", file=sys.stderr)
        return 2
    repo_root = Path(root_result.stdout.strip()).resolve()

    files = collect_source(source)
    if not files:
        print("ERRO: nenhum arquivo permitido encontrado.", file=sys.stderr)
        return 2
    invalid = [path for path in files if is_forbidden_repo_path(path)]
    if invalid:
        print("ERRO: filtro de segurança rejeitou arquivos de origem:", file=sys.stderr)
        print("\n".join(invalid), file=sys.stderr)
        return 3

    protected_files = {
        "README.md", ".gitignore", "sync_from_local.py", "sync_from_local.ps1"
    }
    removed = remove_stale_managed_files(set(files) | protected_files)

    for rel, src in sorted(files.items()):
        target = DEST / Path(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)

    print(f"Arquivos sincronizados: {len(files)}")
    print(f"Arquivos obsoletos removidos: {removed}")
    print("Segredos, bancos, dados e relatórios não foram copiados.")

    if not args.skip_tests:
        print("\nExecutando pytest na cópia sincronizada...", flush=True)
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "-q"],
            cwd=DEST,
        )
        if result.returncode:
            print("Testes falharam. Nenhum commit/push será feito.", file=sys.stderr)
            return result.returncode

    if not args.commit:
        print("Sincronização local concluída; nenhum commit/push solicitado.")
        return 0

    relative = DEST.relative_to(repo_root).as_posix()
    run(["git", "add", "--all", "--", relative], cwd=repo_root)

    staged = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        cwd=repo_root,
    )
    if staged.returncode == 0:
        print("Nenhuma diferença para publicar.")
        return 0
    if staged.returncode != 1:
        raise RuntimeError("Não foi possível verificar as alterações preparadas.")

    staged_paths = subprocess.check_output(
        ["git", "diff", "--cached", "--name-only", "--", relative],
        cwd=repo_root, text=True,
    ).splitlines()
    forbidden_staged = [
        path for path in staged_paths if is_forbidden_repo_path(path)
    ]
    if forbidden_staged:
        print("ERRO: o commit contém arquivos proibidos; publicação cancelada:", file=sys.stderr)
        print("\n".join(forbidden_staged), file=sys.stderr)
        return 5

    branch = subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=repo_root, text=True
    ).strip()
    if not branch:
        print("ERRO: HEAD destacado; faça checkout de uma branch antes de publicar.", file=sys.stderr)
        return 4

    message = "Sync KAEL robot source " + datetime.now().strftime("%Y-%m-%d %H:%M")
    run(["git", "commit", "-m", message], cwd=repo_root)
    run(["git", "push", "origin", branch], cwd=repo_root)
    print(f"Publicação concluída na branch {branch}.")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        raise SystemExit(1)
