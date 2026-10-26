from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from pathlib import Path
from .db import connection, init_db
from .normalization import normalize_consent, normalize_phone
from .worker import run_worker

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def import_csv(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {path}")
    inserted = updated = rejected = unknown_consent = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames:
            raise ValueError("CSV sem cabeçalho")
        with connection() as conn:
            for line_no, row in enumerate(reader, start=2):
                phone = normalize_phone(row.get("telefone") or row.get("phone"))
                if not phone:
                    rejected += 1
                    logging.warning("Linha %s ignorada: telefone inválido", line_no)
                    continue
                consent, source, consent_at = normalize_consent(row.get("consent_status"), row.get("consent_source"), row.get("consent_at"))
                if consent != "opt_in":
                    unknown_consent += 1
                values = (
                    (row.get("nome") or row.get("name") or "").strip(),
                    (row.get("empresa") or row.get("company") or "").strip(), phone,
                    (row.get("nicho") or row.get("niche") or "").strip(),
                    (row.get("cidade") or row.get("city") or "").strip(), consent, source, consent_at,
                )
                existed = conn.execute("SELECT id FROM leads WHERE phone=%s", (phone,)).fetchone()
                conn.execute("""
                    INSERT INTO leads (name,company,phone,niche,city,consent_status,consent_source,consent_at)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (phone) DO UPDATE SET
                      name=CASE WHEN EXCLUDED.name='' THEN leads.name ELSE EXCLUDED.name END,
                      company=CASE WHEN EXCLUDED.company='' THEN leads.company ELSE EXCLUDED.company END,
                      niche=CASE WHEN EXCLUDED.niche='' THEN leads.niche ELSE EXCLUDED.niche END,
                      city=CASE WHEN EXCLUDED.city='' THEN leads.city ELSE EXCLUDED.city END,
                      consent_status=CASE WHEN leads.consent_status='opt_out' THEN 'opt_out' ELSE EXCLUDED.consent_status END,
                      consent_source=COALESCE(EXCLUDED.consent_source,leads.consent_source),
                      consent_at=COALESCE(EXCLUDED.consent_at,leads.consent_at), updated_at=NOW()
                """, values)
                if existed:
                    updated += 1
                else:
                    inserted += 1
    print(f"Importação concluída — novos: {inserted}; atualizados: {updated}; rejeitados: {rejected}; sem opt-in elegível: {unknown_consent}")
    print("Importante: leads sem consentimento documentado não serão enviados para campanhas.")


def create_campaign(name: str, messages_path: Path) -> None:
    with messages_path.open("r", encoding="utf-8") as f:
        messages = json.load(f)
    if not isinstance(messages.get("initial"), str) or not messages["initial"].strip():
        raise ValueError("O JSON precisa conter uma string 'initial' não vazia")
    followups = messages.get("follow_ups", [])
    if not isinstance(followups, list):
        raise ValueError("'follow_ups' precisa ser uma lista")
    previous = 0
    for item in followups:
        days = int(item.get("days_after", 0))
        if days <= previous or not str(item.get("text", "")).strip():
            raise ValueError("Follow-ups precisam ter dias crescentes e texto não vazio")
        previous = days
    with connection() as conn:
        row = conn.execute("INSERT INTO campaigns (name,status,messages) VALUES (%s,'paused',%s) RETURNING id", (name, json.dumps(messages))).fetchone()
    print(f"Campanha #{row['id']} criada PAUSADA. Revise as mensagens antes de ativar.")


def enroll(campaign_id: int) -> None:
    with connection() as conn:
        campaign = conn.execute("SELECT id,status FROM campaigns WHERE id=%s", (campaign_id,)).fetchone()
        if not campaign:
            raise ValueError(f"Campanha #{campaign_id} não encontrada")
        if campaign["status"] != "paused":
            raise ValueError("Por segurança, só é possível enfileirar leads com a campanha pausada")
        rows = conn.execute("""
            SELECT l.id FROM leads l
            LEFT JOIN suppressions s ON s.phone=l.phone
            WHERE l.active=TRUE AND l.consent_status='opt_in'
              AND l.consent_source IS NOT NULL AND l.consent_at IS NOT NULL
              AND s.phone IS NULL AND l.replied_at IS NULL
        """).fetchall()
        for row in rows:
            conn.execute("INSERT INTO message_jobs (lead_id,campaign_id,step_no,due_at) VALUES (%s,%s,0,NOW()) ON CONFLICT (lead_id,campaign_id,step_no) DO NOTHING",
                         (row["id"], campaign_id))
    print(f"Enfileiramento concluído: {len(rows)} leads elegíveis avaliados. A campanha permanece pausada.")


def preview(campaign_id: int) -> None:
    with connection() as conn:
        rows = conn.execute("""
            SELECT j.id,j.step_no,j.due_at,j.status,l.name,l.company,l.phone,l.niche,l.city,
                   c.messages,c.name AS campaign_name
            FROM message_jobs j JOIN leads l ON l.id=j.lead_id JOIN campaigns c ON c.id=j.campaign_id
            WHERE j.campaign_id=%s AND j.status='pending' ORDER BY j.due_at,j.id LIMIT 25
        """, (campaign_id,)).fetchall()
    if not rows:
        print("Nenhuma mensagem pendente nesta campanha.")
        return
    from .normalization import render_template
    for row in rows:
        if row["step_no"] == 0:
            template = row["messages"].get("initial", "")
        else:
            followups = row["messages"].get("follow_ups", [])
            idx = row["step_no"] - 1
            template = followups[idx].get("text", "") if idx < len(followups) else ""
        rendered = render_template(template, {"nome": row["name"], "empresa": row["company"], "nicho": row["niche"], "cidade": row["city"]}) if template else "[TEMPLATE AUSENTE]"
        print(f"#{row['id']} | {row['phone']} | etapa {row['step_no']} | vence {row['due_at']} | {rendered}")


def activate(campaign_id: int) -> None:
    with connection() as conn:
        row = conn.execute("UPDATE campaigns SET status='active' WHERE id=%s AND status='paused' RETURNING id", (campaign_id,)).fetchone()
    if not row:
        raise ValueError("Campanha não encontrada ou não está pausada")
    print(f"Campanha #{campaign_id} ativada. O envio real continua condicionado às travas no .env.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="kael", description="Robô Python da Visível — campanhas controladas de WhatsApp")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="Criar tabelas no PostgreSQL")
    imp = sub.add_parser("import-csv", help="Importar ou atualizar leads a partir de CSV")
    imp.add_argument("path", type=Path)
    camp = sub.add_parser("campaign-create", help="Criar campanha pausada")
    camp.add_argument("--name", required=True)
    camp.add_argument("--messages", type=Path, required=True)
    enroll_cmd = sub.add_parser("campaign-enroll", help="Enfileirar leads elegíveis")
    enroll_cmd.add_argument("--campaign-id", type=int, required=True)
    prev = sub.add_parser("queue-preview", help="Pré-visualizar mensagens pendentes sem enviar")
    prev.add_argument("--campaign-id", type=int, required=True)
    act = sub.add_parser("campaign-activate", help="Ativar campanha (envio real ainda tem travas adicionais)")
    act.add_argument("--campaign-id", type=int, required=True)
    bd = sub.add_parser("browser-draft", help="Abrir um único contato autorizado no WhatsApp Web e preencher um rascunho para revisão manual")
    bd.add_argument("--phone", required=True, help="Telefone registrado na base")
    bd.add_argument("--message", required=True, help="Mensagem de rascunho; aceita {nome}, {empresa}, {nicho} e {cidade}")
    worker = sub.add_parser("worker", help="Executar o worker da fila")
    worker.add_argument("--once", action="store_true")
    serve = sub.add_parser("serve", help="Executar API de health check e webhook")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    sheetplan = sub.add_parser("plan-sheet", help="Planejar qualificação/oferta/mensagem na planilha Google (sem gravar por padrão)")
    sheetplan.add_argument("--source-tab", default=None, help="Aba de origem; padrão GOOGLE_SOURCE_TAB")
    sheetplan.add_argument("--output-tab", default=None, help="Aba de saída; padrão GOOGLE_OUTPUT_TAB")
    sheetplan.add_argument("--limit", type=int, default=0, help="Máximo de registros; 0 processa todos")
    sheetplan.add_argument("--write", action="store_true", help="Gravar/atualizar a aba de saída; sem isso apenas mostra prévia")
    consolidate = sub.add_parser("consolidate-sheet", help="Auditar candidatos a duplicidade e preparar prévia conservadora no Google Sheets")
    consolidate.add_argument("--source-tab", default=None, help="Aba de origem; padrão GOOGLE_CONSOLIDATION_SOURCE_TAB")
    consolidate.add_argument("--output-tab", default=None, help="Aba de saída; padrão GOOGLE_CONSOLIDATION_OUTPUT_TAB")
    consolidate.add_argument("--limit", type=int, default=0, help="Limita somente a prévia; 0 lê todos os registros")
    consolidate.add_argument("--write", action="store_true", help="Grava a saída explicitamente; sem isso é somente leitura")
    consolidate.add_argument("--confirm-overwrite", action="store_true", help="Confirma sobrescrever a aba de saída caso ela já exista")
    lead_worker = sub.add_parser("lead-worker", help="Validar leads continuamente e preparar rascunhos; nunca envia WhatsApp")
    lead_worker.add_argument("--once", action="store_true", help="Executa apenas um ciclo e encerra")
    lead_worker.add_argument("--write", action="store_true", help="Autoriza atualizar a aba de planejamento; sem isso é simulação")
    lead_worker.add_argument("--source-tab", default=None, help="Aba bruta de origem; padrão GOOGLE_CONSOLIDATION_SOURCE_TAB")
    lead_worker.add_argument("--output-tab", default="kael_planejamento_continuo", help="Aba gerenciada de saída; padrão kael_planejamento_continuo")
    lead_worker.add_argument("--interval-seconds", type=int, default=300, help="Intervalo do worker (60 a 86400 segundos; padrão 300)")
    lead_worker.add_argument("--limit", type=int, default=0, help="Amostra limitada só para uma execução de simulação")
    lead_worker.add_argument("--state-db", type=Path, default=Path("data/kael_lead_worker.sqlite3"), help="Arquivo SQLite local para checkpoints")
    lead_worker.add_argument("--confirm-overwrite", action="store_true", help="Autoriza usar a aba de saída se ela já existir")
    args = parser.parse_args()
    try:
        if args.command == "init-db":
            init_db(); print("Banco inicializado.")
        elif args.command == "import-csv": import_csv(args.path)
        elif args.command == "campaign-create": create_campaign(args.name, args.messages)
        elif args.command == "campaign-enroll": enroll(args.campaign_id)
        elif args.command == "queue-preview": preview(args.campaign_id)
        elif args.command == "campaign-activate": activate(args.campaign_id)
        elif args.command == "browser-draft":
            from .browser_assist import prepare_manual_draft
            prepare_manual_draft(args.phone, args.message)
        elif args.command == "plan-sheet":
            from .sheets_planner import run_plan_sheet
            run_plan_sheet(write=args.write, source_tab=args.source_tab, output_tab=args.output_tab, limit=args.limit)
        elif args.command == "consolidate-sheet":
            from .lead_consolidation import run_consolidation_sheet
            run_consolidation_sheet(
                write=args.write,
                confirm_overwrite=args.confirm_overwrite,
                source_tab=args.source_tab,
                output_tab=args.output_tab,
                limit=args.limit,
            )
        elif args.command == "worker": run_worker(args.once)
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
            import uvicorn
            from .web import app
            uvicorn.run(app, host=args.host, port=args.port)
    except Exception as exc:
        logging.error("Operação falhou: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
