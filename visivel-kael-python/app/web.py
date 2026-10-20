from __future__ import annotations

import csv
import hmac
import html
import io
import logging
import secrets
from datetime import datetime
from urllib.parse import parse_qs, urlencode

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from .config import settings
from .db import connection
from .draft_review import MAX_BATCH_LIMIT, build_whatsapp_url, render_review_message, validate_template
from .normalization import normalize_phone
from .webhook_utils import extract_message, is_opt_out_message

log = logging.getLogger("kael.webhook")
app = FastAPI(title="Visível / KAEL", version="0.2.0")

DEFAULT_TEMPLATE = (
    "Olá, {nome}! Tudo bem? Sou da Visível. Ajudamos empresas como a {empresa} "
    "a melhorar sua presença digital. Faz sentido eu te enviar uma ideia rápida, sem compromisso? "
    "Se preferir não receber mensagens, responda SAIR."
)
PAGE_SIZE = 30
CSRF_TOKEN = secrets.token_urlsafe(32)


def _e(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def _form_values(body: bytes) -> dict[str, str]:
    parsed = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
    return {key: values[-1] if values else "" for key, values in parsed.items()}


def _csrf_hidden() -> str:
    return f'<input type="hidden" name="csrf_token" value="{_e(CSRF_TOKEN)}">'


def _validate_csrf(form: dict[str, str]) -> None:
    token = form.get("csrf_token", "")
    if not token or not hmac.compare_digest(token, CSRF_TOKEN):
        raise HTTPException(status_code=403, detail="Formulário expirado ou token de segurança inválido. Atualize a página e tente novamente.")


def _layout(title: str, content: str) -> str:
    return f"""<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{_e(title)} · Visível / KAEL</title>
<style>
:root {{ color-scheme: light; --ink:#172033; --muted:#5e6b80; --line:#dbe2ea; --blue:#164e91; --pale:#f3f6fa; --green:#e7f6ed; --red:#fff0ef; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:#f5f7fa; color:var(--ink); font:15px/1.45 system-ui,-apple-system,Segoe UI,sans-serif; }}
header {{ background:#0b1f3a; color:white; padding:22px max(18px,calc((100vw - 1200px)/2)); }}
header h1 {{ margin:0 0 4px; font-size:23px; }} header p {{ margin:0; color:#d2dced; }}
main {{ max-width:1200px; margin:22px auto; padding:0 16px 60px; }}
.panel {{ background:white; border:1px solid var(--line); border-radius:14px; padding:18px; margin-bottom:16px; box-shadow:0 2px 8px #14243a0a; }}
h2 {{ font-size:18px; margin:0 0 12px; }} h3 {{ font-size:16px; margin:0 0 6px; }}
p {{ margin:6px 0 12px; }} .muted {{ color:var(--muted); font-size:13px; }}
label {{ display:block; font-weight:600; margin:10px 0 5px; }}
input, textarea, select {{ width:100%; border:1px solid #bdc8d7; border-radius:8px; padding:10px; color:var(--ink); background:white; font:inherit; }}
textarea {{ min-height:110px; resize:vertical; }}
button, .button {{ border:0; border-radius:8px; background:var(--blue); color:white; padding:9px 13px; font:inherit; cursor:pointer; text-decoration:none; display:inline-block; }}
button.secondary, .button.secondary {{ background:#e9eef5; color:#17304f; }}
button.danger, .button.danger {{ background:#9b2828; color:white; }}
button:focus, a:focus, input:focus, textarea:focus {{ outline:2px solid #6ca6e8; outline-offset:2px; }}
.row {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:12px; }}
.metrics {{ display:flex; flex-wrap:wrap; gap:8px; margin:12px 0; }}
.metric {{ border:1px solid var(--line); border-radius:9px; padding:9px 12px; background:white; }}
.metric strong {{ font-size:18px; display:block; }} .filters {{ display:grid; grid-template-columns:1fr 220px auto; align-items:end; gap:10px; }}
.draft {{ border:1px solid var(--line); border-radius:12px; padding:15px; margin:12px 0; background:#fff; }}
.draft-head {{ display:flex; gap:14px; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; }}
.draft-meta {{ color:var(--muted); font-size:13px; }}
.status {{ display:inline-block; border-radius:20px; padding:4px 9px; font-size:12px; font-weight:700; }}
.status.pending {{ background:#fff4d6; color:#765000; }} .status.approved {{ background:var(--green); color:#126134; }} .status.rejected {{ background:var(--red); color:#9b2828; }}
.actions {{ display:flex; flex-wrap:wrap; gap:8px; margin-top:10px; }}
.notice {{ border-radius:9px; background:#e8f2ff; padding:12px; margin-bottom:14px; }}
.warning {{ border-left:4px solid #df9d22; background:#fff9e9; padding:12px; border-radius:6px; }}
.pager {{ display:flex; justify-content:space-between; gap:12px; align-items:center; margin-top:14px; }}
@media(max-width:720px) {{ .row,.filters {{ grid-template-columns:1fr; }} header {{ padding:18px; }} main {{ margin-top:12px; }} .panel {{ padding:14px; }} }}
</style>
</head>
<body>
<header><h1>Visível · Central de rascunhos KAEL</h1><p>Prepare, revise e organize mensagens sem enviá-las automaticamente.</p></header>
<main>{content}</main>
</body></html>"""


def _generation_form(default_template: str) -> str:
    label_time = datetime.now().strftime("%d/%m/%Y %H:%M")
    return f"""
    <form method="post" action="/drafts/generate">
      {_csrf_hidden()}
      <div class="row">
        <div><label for="batch_name">Nome do lote</label><input id="batch_name" name="batch_name" maxlength="120" required value="Prospecção {_e(label_time)}"></div>
        <div><label for="limit">Máximo de leads a preparar (1–{MAX_BATCH_LIMIT})</label><input id="limit" type="number" name="limit" min="1" max="{MAX_BATCH_LIMIT}" value="100" required></div>
      </div>
      <div class="row">
        <div><label for="niche_filter">Filtrar nicho (opcional; contém)</label><input id="niche_filter" name="niche_filter" maxlength="120" placeholder="Ex.: saúde"></div>
        <div><label for="city_filter">Filtrar cidade (opcional; contém)</label><input id="city_filter" name="city_filter" maxlength="120" placeholder="Ex.: Guarulhos"></div>
      </div>
      <label for="message_template">Modelo da mensagem</label>
      <textarea id="message_template" name="message_template" maxlength="4000" required>{_e(default_template)}</textarea>
      <p class="muted">Campos disponíveis: {{nome}}, {{empresa}}, {{nicho}}, {{cidade}}. Se um campo usado no texto estiver vazio, aquele lead será ignorado para evitar mensagens incompletas.</p>
      <div class="actions"><button type="submit">Gerar rascunhos elegíveis</button></div>
    </form>"""


def _set_opt_out(phone: str, text: str) -> None:
    with connection() as conn:
        lead = conn.execute("SELECT id FROM leads WHERE phone=%s", (phone,)).fetchone()
        conn.execute("INSERT INTO suppressions (phone,reason) VALUES (%s,%s) ON CONFLICT (phone) DO UPDATE SET reason=EXCLUDED.reason, created_at=NOW()",
                     (phone, f"Pedido do contato: {text[:120]}"))
        conn.execute("UPDATE leads SET consent_status='opt_out', active=FALSE, updated_at=NOW() WHERE phone=%s", (phone,))
        if lead:
            conn.execute("UPDATE message_jobs SET status='cancelled', last_error='Opt-out recebido' WHERE lead_id=%s AND status IN ('pending','processing')", (lead["id"],))
            conn.execute("UPDATE draft_reviews SET status='rejected', review_note='Contato pediu descadastro', updated_at=NOW(), reviewed_at=NOW() WHERE lead_id=%s AND status IN ('pending','approved')", (lead["id"],))
            conn.execute("INSERT INTO message_events (lead_id,direction,status,message_text) VALUES (%s,'inbound','opt_out',%s)", (lead["id"], text[:4000]))


def _record_reply(phone: str, text: str, provider_message_id: str | None) -> None:
    with connection() as conn:
        lead = conn.execute("SELECT id FROM leads WHERE phone=%s", (phone,)).fetchone()
        if not lead:
            log.info("Resposta recebida de número não importado; não criando lead automaticamente")
            return
        conn.execute("UPDATE leads SET replied_at=COALESCE(replied_at,NOW()), updated_at=NOW() WHERE id=%s", (lead["id"],))
        conn.execute("UPDATE message_jobs SET status='cancelled', last_error='Resposta recebida; cadência interrompida' WHERE lead_id=%s AND status IN ('pending','processing')", (lead["id"],))
        conn.execute("UPDATE draft_reviews SET status='rejected', review_note='Resposta recebida; revisar conversa antes de novo contato', updated_at=NOW(), reviewed_at=NOW() WHERE lead_id=%s AND status IN ('pending','approved')", (lead["id"],))
        conn.execute("INSERT INTO message_events (lead_id,direction,status,message_text,provider_message_id) VALUES (%s,'inbound','received',%s,%s)",
                     (lead["id"], text[:4000], provider_message_id))


@app.get("/", include_in_schema=False)
def home() -> RedirectResponse:
    return RedirectResponse(url="/drafts", status_code=303)


@app.get("/drafts", response_class=HTMLResponse)
def drafts_page(batch_id: int | None = None, page: int = 1, status: str = "all", q: str = "", notice: str = "") -> HTMLResponse:
    status = status if status in {"all", "pending", "approved", "rejected"} else "all"
    page = max(1, page)
    with connection() as conn:
        batches = conn.execute("SELECT id,name,created_at FROM draft_batches ORDER BY id DESC LIMIT 100").fetchall()
        if not batches:
            content = f"""
            <section class="panel">
              <h2>Começar: gerar um lote de mensagens</h2>
              <p>O KAEL seleciona contatos ativos com opt-in documentado, sem resposta registrada e fora da lista de supressão. Ele cria rascunhos na central; não envia mensagens.</p>
              <div class="warning"><strong>Importante:</strong> gerar rascunhos não envia nada pelo WhatsApp. Revise os textos e confirme o destinatário antes de qualquer envio manual.</div>
              {_generation_form(DEFAULT_TEMPLATE)}
            </section>
            <section class="panel"><h2>Antes de gerar</h2><p class="muted">É necessário iniciar o PostgreSQL, executar novamente init-db após atualizar o código e importar leads com consentimento documentado. Para testar sem uma base real, use contatos fictícios e não envie mensagens.</p></section>
            """
            return HTMLResponse(_layout("Central de rascunhos", content))
        selected_batch = batch_id if batch_id and any(int(b["id"]) == batch_id for b in batches) else int(batches[0]["id"])
        batch = conn.execute("SELECT * FROM draft_batches WHERE id=%s", (selected_batch,)).fetchone()
        counts = conn.execute("SELECT status,COUNT(*) AS n FROM draft_reviews WHERE batch_id=%s GROUP BY status", (selected_batch,)).fetchall()
        count_map = {r["status"]: r["n"] for r in counts}
        search_like = f"%{q.strip()}%"
        where_sql = """
          FROM draft_reviews d JOIN leads l ON l.id=d.lead_id
          LEFT JOIN suppressions s ON s.phone=l.phone
          WHERE d.batch_id=%s
            AND (%s='all' OR d.status=%s)
            AND (%s='' OR l.name ILIKE %s OR l.company ILIKE %s OR l.phone ILIKE %s OR l.niche ILIKE %s)
        """
        params = (selected_batch, status, status, q.strip(), search_like, search_like, search_like, search_like)
        total = conn.execute("SELECT COUNT(*) AS n " + where_sql, params).fetchone()["n"]
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, pages)
        rows = conn.execute(
            """
            SELECT d.id AS draft_id,d.batch_id,d.lead_id,d.message_text,d.status,d.review_note,d.created_at,
                   l.name,l.company,l.phone,l.niche,l.city,l.active,l.consent_status,l.consent_source,l.consent_at,l.replied_at,
                   (s.phone IS NOT NULL) AS suppressed
            """ + where_sql + """
            ORDER BY CASE d.status WHEN 'pending' THEN 0 WHEN 'approved' THEN 1 ELSE 2 END, d.id
            LIMIT %s OFFSET %s
            """,
            params + (PAGE_SIZE, (page - 1) * PAGE_SIZE),
        ).fetchall()

    counts_html = "".join(
        f'<div class="metric"><span class="muted">{label}</span><strong>{int(value)}</strong></div>'
        for label, value in (
            ("Pendentes", count_map.get("pending", 0)),
            ("Aprovados", count_map.get("approved", 0)),
            ("Rejeitados", count_map.get("rejected", 0)),
            ("Total", sum(count_map.values())),
        )
    )
    batches_options = "".join(
        f'<option value="{int(b["id"])}" {"selected" if int(b["id"]) == selected_batch else ""}>{int(b["id"])} · {_e(b["name"])}</option>'
        for b in batches
    )
    notices_html = f'<div class="notice">{_e(notice)}</div>' if notice else ""
    row_cards = []
    for row in rows:
        problem = None
        if not row["active"]:
            problem = "Contato inativo"
        elif row["consent_status"] != "opt_in" or not row["consent_source"] or not row["consent_at"]:
            problem = "Consentimento ausente ou incompleto"
        elif row["replied_at"] is not None:
            problem = "Já há resposta registrada"
        elif row["suppressed"]:
            problem = "Número na lista de supressão"
        safe_url = ""
        if row["status"] == "approved" and not problem:
            try:
                safe_url = build_whatsapp_url(row["phone"], row["message_text"])
            except ValueError:
                problem = "Telefone ou mensagem inválidos"
        status_label = {"pending": "Pendente", "approved": "Aprovado para revisão no WhatsApp", "rejected": "Rejeitado"}.get(row["status"], row["status"])
        status_html = f'<span class="status {_e(row["status"])}">{_e(status_label)}</span>'
        unavailable = f'<div class="warning">Este rascunho não pode ser aberto: {_e(problem)}. Reavalie o cadastro.</div>' if problem else ""
        open_link = f'<a class="button" href="{_e(safe_url)}" target="_blank" rel="noopener noreferrer">Abrir WhatsApp com texto preenchido</a>' if safe_url and row["status"] == "approved" else ""
        row_cards.append(f"""
        <article class="draft">
          <div class="draft-head">
            <div><h3>{_e(row["name"] or "Nome não informado")} · {_e(row["company"] or "Empresa não informada")}</h3>
              <div class="draft-meta">Telefone: {_e(row["phone"])} · Nicho: {_e(row["niche"] or "—")} · Cidade: {_e(row["city"] or "—")} · Lead #{int(row["lead_id"])}</div>
            </div>
            <div>{status_html}</div>
          </div>
          {unavailable}
          <form method="post" action="/drafts/{int(row["draft_id"])}/review">
            {_csrf_hidden()}
            <label for="message-{int(row["draft_id"])}">Mensagem personalizada (pode editar)</label>
            <textarea id="message-{int(row["draft_id"])}" name="message_text" maxlength="4000" required>{_e(row["message_text"])}</textarea>
            <label for="note-{int(row["draft_id"])}">Nota interna (opcional)</label>
            <input id="note-{int(row["draft_id"])}" name="review_note" maxlength="500" value="{_e(row["review_note"] or "")}" placeholder="Ex.: falta confirmar o serviço de interesse">
            <div class="actions">
              <button type="submit" name="status" value="pending" class="secondary">Salvar como pendente</button>
              <button type="submit" name="status" value="approved">Aprovar rascunho</button>
              <button type="submit" name="status" value="rejected" class="danger">Rejeitar</button>
              {open_link}
            </div>
          </form>
        </article>""")
    pagination = f"""
      <div class="pager">
        <span class="muted">Página {page} de {pages} · {total} resultado(s)</span>
        <div class="actions">
          {"<a class='button secondary' href='/drafts?" + urlencode({"batch_id":selected_batch,"page":page-1,"status":status,"q":q}) + "'>Anterior</a>" if page > 1 else ""}
          {"<a class='button secondary' href='/drafts?" + urlencode({"batch_id":selected_batch,"page":page+1,"status":status,"q":q}) + "'>Próxima</a>" if page < pages else ""}
        </div>
      </div>"""
    content = f"""
      {notices_html}
      <section class="panel">
        <h2>Central de revisão</h2>
        <p>Revise as mensagens de todo o lote nesta tela. Aprovar significa liberar o botão para abrir o WhatsApp com o texto preenchido; não envia a mensagem.</p>
        <form method="get" action="/drafts" class="row">
          <div><label for="batch_id">Lote</label><select id="batch_id" name="batch_id">{batches_options}</select></div>
          <div style="display:flex;align-items:end"><button type="submit">Abrir lote</button></div>
        </form>
        <div class="metrics">{counts_html}</div>
        <div class="actions">
          <a class="button secondary" href="/drafts/export.csv?batch_id={selected_batch}">Exportar aprovados para CSV</a>
          <a class="button secondary" href="/drafts">Gerar outro lote</a>
        </div>
      </section>
      <section class="panel">
        <h2>Lote #{int(batch["id"])} · {_e(batch["name"])}</h2>
        <p class="muted">Criado em {_e(batch["created_at"])}. Nenhum texto foi enviado por esta central.</p>
        <form method="get" action="/drafts" class="filters">
          <input type="hidden" name="batch_id" value="{selected_batch}">
          <div><label for="q">Buscar por nome, empresa, telefone ou nicho</label><input id="q" name="q" value="{_e(q)}" placeholder="Ex.: clínica, 5511..."></div>
          <div><label for="status">Status</label><select id="status" name="status">
            <option value="all" {"selected" if status == "all" else ""}>Todos</option>
            <option value="pending" {"selected" if status == "pending" else ""}>Pendentes</option>
            <option value="approved" {"selected" if status == "approved" else ""}>Aprovados</option>
            <option value="rejected" {"selected" if status == "rejected" else ""}>Rejeitados</option>
          </select></div>
          <button type="submit">Filtrar</button>
        </form>
      </section>
      {''.join(row_cards) if row_cards else '<section class="panel">Nenhum rascunho corresponde a este filtro.</section>'}
      {pagination}
      <section class="panel">
        <h2>Gerar outro lote</h2>
        {_generation_form(DEFAULT_TEMPLATE)}
      </section>
    """
    return HTMLResponse(_layout("Central de rascunhos", content))


@app.post("/drafts/generate")
async def generate_drafts(request: Request) -> RedirectResponse:
    form = _form_values(await request.body())
    _validate_csrf(form)
    name = form.get("batch_name", "").strip()[:120] or "Lote sem nome"
    template = form.get("message_template", "").strip()
    niche_filter = form.get("niche_filter", "").strip()[:120]
    city_filter = form.get("city_filter", "").strip()[:120]
    try:
        limit = int(form.get("limit", "100"))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="O limite deve ser um número inteiro.") from exc
    if not 1 <= limit <= MAX_BATCH_LIMIT:
        raise HTTPException(status_code=400, detail=f"O limite deve ficar entre 1 e {MAX_BATCH_LIMIT}.")
    validate_template(template)
    with connection() as conn:
        candidates = conn.execute(
            """
            SELECT l.id,l.name,l.company,l.phone,l.niche,l.city
            FROM leads l
            LEFT JOIN suppressions s ON s.phone=l.phone
            WHERE l.active=TRUE
              AND l.consent_status='opt_in'
              AND l.consent_source IS NOT NULL AND l.consent_at IS NOT NULL
              AND l.replied_at IS NULL
              AND s.phone IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM draft_reviews existing
                  WHERE existing.lead_id=l.id AND existing.status IN ('pending','approved')
              )
              AND (%s='' OR l.niche ILIKE %s)
              AND (%s='' OR l.city ILIKE %s)
            ORDER BY l.id
            LIMIT %s
            """,
            (niche_filter, f"%{niche_filter}%", city_filter, f"%{city_filter}%", min(limit * 5, 50000)),
        ).fetchall()
        prepared: list[tuple[int, str]] = []
        skipped_missing = 0
        for lead in candidates:
            mapped = {"nome": lead["name"], "empresa": lead["company"], "nicho": lead["niche"], "cidade": lead["city"]}
            try:
                message = render_review_message(template, mapped)
            except ValueError as exc:
                if str(exc).startswith("Faltam dados"):
                    skipped_missing += 1
                    continue
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            prepared.append((lead["id"], message))
            if len(prepared) >= limit:
                break
        if not prepared:
            note = "Nenhum lead elegível com os dados necessários foi encontrado."
            if skipped_missing:
                note += f" {skipped_missing} lead(s) ignorado(s) por falta de dados usados no modelo."
            return RedirectResponse(url="/drafts?" + urlencode({"notice": note}), status_code=303)
        batch = conn.execute(
            "INSERT INTO draft_batches (name,message_template,niche_filter,city_filter) VALUES (%s,%s,%s,%s) RETURNING id",
            (name, template, niche_filter, city_filter),
        ).fetchone()
        for lead_id, message in prepared:
            conn.execute(
                "INSERT INTO draft_reviews (batch_id,lead_id,message_text,status) VALUES (%s,%s,%s,'pending') ON CONFLICT (batch_id,lead_id) DO NOTHING",
                (batch["id"], lead_id, message),
            )
        note = f"Lote criado: {len(prepared)} rascunho(s) pendente(s). Nenhuma mensagem foi enviada."
        if skipped_missing:
            note += f" Ignorados por falta de dados: {skipped_missing}."
    return RedirectResponse(url="/drafts?" + urlencode({"batch_id": batch["id"], "notice": note}), status_code=303)


@app.post("/drafts/{draft_id}/review")
async def review_draft(draft_id: int, request: Request) -> RedirectResponse:
    form = _form_values(await request.body())
    _validate_csrf(form)
    message = form.get("message_text", "").strip()
    status = form.get("status", "pending")
    review_note = form.get("review_note", "").strip()[:500] or None
    if status not in {"pending", "approved", "rejected"}:
        raise HTTPException(status_code=400, detail="Status de revisão inválido.")
    if not message:
        raise HTTPException(status_code=400, detail="A mensagem não pode ficar vazia.")
    if len(message) > 4000:
        raise HTTPException(status_code=400, detail="A mensagem deve ter no máximo 4.000 caracteres.")
    with connection() as conn:
        row = conn.execute(
            """
            SELECT d.id,d.batch_id,l.phone,l.active,l.consent_status,l.consent_source,l.consent_at,l.replied_at,
                   (s.phone IS NOT NULL) AS suppressed
            FROM draft_reviews d JOIN leads l ON l.id=d.lead_id
            LEFT JOIN suppressions s ON s.phone=l.phone
            WHERE d.id=%s
            """,
            (draft_id,),
        ).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="Rascunho não encontrado.")
        if status == "approved":
            problem = None
            if not row["active"]:
                problem = "O contato está inativo."
            elif row["consent_status"] != "opt_in" or not row["consent_source"] or not row["consent_at"]:
                problem = "O consentimento não está documentado corretamente."
            elif row["replied_at"] is not None:
                problem = "Há uma resposta registrada para este contato."
            elif row["suppressed"]:
                problem = "O número está na lista de supressão."
            if problem:
                return RedirectResponse(url="/drafts?" + urlencode({"batch_id": row["batch_id"], "notice": "Aprovação bloqueada: " + problem}), status_code=303)
        conn.execute(
            """
            UPDATE draft_reviews SET message_text=%s,status=%s,review_note=%s,
              updated_at=NOW(), reviewed_at=CASE WHEN %s IN ('approved','rejected') THEN NOW() ELSE NULL END
            WHERE id=%s
            """,
            (message, status, review_note, status, draft_id),
        )
    msg = {"pending": "Rascunho salvo como pendente.", "approved": "Rascunho aprovado. Ele ainda não foi enviado.", "rejected": "Rascunho rejeitado."}[status]
    return RedirectResponse(url="/drafts?" + urlencode({"batch_id": row["batch_id"], "notice": msg}), status_code=303)


@app.get("/drafts/export.csv")
def export_approved_drafts(batch_id: int) -> Response:
    out = io.StringIO(newline="")
    writer = csv.writer(out)
    writer.writerow(["lead_id", "nome", "empresa", "telefone", "nicho", "cidade", "mensagem", "status_revisao"])
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT l.id,l.name,l.company,l.phone,l.niche,l.city,d.message_text,d.status
            FROM draft_reviews d JOIN leads l ON l.id=d.lead_id
            LEFT JOIN suppressions s ON s.phone=l.phone
            WHERE d.batch_id=%s AND d.status='approved'
              AND l.active=TRUE AND l.consent_status='opt_in'
              AND l.consent_source IS NOT NULL AND l.consent_at IS NOT NULL
              AND l.replied_at IS NULL AND s.phone IS NULL
            ORDER BY d.id
            """,
            (batch_id,),
        ).fetchall()
    for row in rows:
        writer.writerow([row["id"], row["name"], row["company"], row["phone"], row["niche"], row["city"], row["message_text"], row["status"]])
    return Response(content="\ufeff" + out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="kael-rascunhos-aprovados-lote-{batch_id}.csv"'})


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "visivel-kael", "send_mode": settings.send_mode}


@app.post("/webhook/evolution")
async def evolution_webhook(request: Request, x_webhook_secret: str | None = Header(default=None)) -> dict:
    expected = settings.webhook_secret
    if not expected or not x_webhook_secret or not hmac.compare_digest(x_webhook_secret, expected):
        raise HTTPException(status_code=401, detail="Webhook não autorizado")
    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="JSON inválido") from exc
    event_name = str(payload.get("event", "")).lower()
    if event_name and "messages.upsert" not in event_name:
        return {"ok": True, "ignored": True}
    extracted = extract_message(payload)
    if not extracted:
        return {"ok": True, "ignored": True, "reason": "evento sem texto/mensagem"}
    remote, text, from_me, provider_id = extracted
    if from_me:
        return {"ok": True, "ignored": True, "reason": "mensagem enviada pela própria instância"}
    if "@g.us" in remote or "status@broadcast" in remote:
        return {"ok": True, "ignored": True, "reason": "grupo/status ignorado"}
    phone = normalize_phone(remote.split("@")[0])
    if not phone:
        return {"ok": True, "ignored": True, "reason": "telefone inválido"}
    if is_opt_out_message(text):
        _set_opt_out(phone, text)
        return {"ok": True, "opt_out": True}
    _record_reply(phone, text, provider_id)
    return {"ok": True, "reply_recorded": True}
