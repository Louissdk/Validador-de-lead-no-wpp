from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import quote

from .db import connection
from .normalization import normalize_phone, render_template


def eligibility_problem(lead: dict | None, suppressed: bool = False) -> str | None:
    """Return a human-readable reason when a single-contact draft is not allowed."""
    if lead is None:
        return "Contato não cadastrado. Importe e valide o contato antes de continuar."
    if not lead.get("active"):
        return "Contato inativo."
    if lead.get("consent_status") != "opt_in":
        return "Não há consentimento opt-in registrado para este contato."
    if not lead.get("consent_source") or not lead.get("consent_at"):
        return "O consentimento não possui origem e data documentadas."
    if lead.get("replied_at") is not None:
        return "Este contato já respondeu; revise a conversa manualmente antes de um novo contato."
    if suppressed:
        return "Este número consta na lista de supressão/descadastro."
    return None


def prepare_manual_draft(raw_phone: str, raw_message: str) -> None:
    """Open one authorized chat and fill its composer. This function never sends."""
    phone = normalize_phone(raw_phone)
    if not phone:
        raise ValueError("Telefone inválido. Informe DDD e número com código do país, se possível.")
    message = str(raw_message or "").strip()
    if not message:
        raise ValueError("A mensagem não pode estar vazia.")
    if len(message) > 4000:
        raise ValueError("Mensagem muito longa para o modo assistido (máximo de 4.000 caracteres).")

    with connection() as conn:
        lead = conn.execute(
            """
            SELECT l.id,l.phone,l.name,l.company,l.niche,l.city,l.consent_status,
                   l.consent_source,l.consent_at,l.active,l.replied_at,
                   (s.phone IS NOT NULL) AS suppressed
            FROM leads l
            LEFT JOIN suppressions s ON s.phone=l.phone
            WHERE l.phone=%s
            """,
            (phone,),
        ).fetchone()

    problem = eligibility_problem(lead, bool(lead and lead["suppressed"]))
    if problem:
        raise PermissionError(problem)

    rendered = render_template(
        message,
        {
            "nome": lead["name"],
            "empresa": lead["company"],
            "nicho": lead["niche"],
            "cidade": lead["city"],
        },
    )

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright não está instalado. Rode: pip install playwright "
            "e depois: playwright install chromium"
        ) from exc

    profile_dir = Path(
        os.getenv("WHATSAPP_PROFILE_DIR", "data/whatsapp-profile")
    ).expanduser().resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)

    context = None
    try:
        with sync_playwright() as playwright:
            context = playwright.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                headless=False,
                viewport=None,
                args=["--start-maximized"],
            )
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(
                f"https://web.whatsapp.com/send?phone={quote(phone)}",
                wait_until="domcontentloaded",
                timeout=60000,
            )
            print(
                "WhatsApp Web aberto. Se solicitado, escaneie o QR code no navegador. "
                "Aguarde o chat carregar."
            )
            try:
                page.wait_for_function(
                    """() =>
                      !!document.querySelector('#main footer div[contenteditable="true"]') ||
                      !!document.querySelector('footer [contenteditable="true"][role="textbox"]')
                    """,
                    timeout=180000,
                )
            except PlaywrightTimeoutError as exc:
                raise RuntimeError(
                    "A caixa de mensagem não apareceu em 3 minutos. Verifique o login, "
                    "a conexão e se o número tem uma conversa disponível no WhatsApp Web."
                ) from exc

            composer = None
            for selector in (
                '#main footer div[contenteditable="true"]',
                'footer [contenteditable="true"][role="textbox"]',
            ):
                locator = page.locator(selector)
                if locator.count() > 0 and locator.last.is_visible():
                    composer = locator.last
                    break
            if composer is None:
                raise RuntimeError(
                    "Não encontrei a caixa de mensagem. A interface do WhatsApp Web pode ter mudado."
                )

            existing_text = (composer.inner_text() or "").strip()
            if existing_text:
                raise RuntimeError(
                    "A caixa de mensagem já contém um rascunho. Por segurança, não o sobrescrevi."
                )

            composer.fill(rendered)
            page.bring_to_front()
            print("")
            print(f"Contato autorizado: {lead['name'] or lead['phone']} ({phone})")
            print("A mensagem foi colocada na caixa de texto, mas NÃO foi enviada pelo robô.")
            print("Revise a conversa e clique manualmente em Enviar somente se estiver correto.")
            input("Depois de enviar manualmente (ou apagar o rascunho), pressione ENTER para fechar o navegador. ")
    finally:
        if context is not None:
            try:
                context.close()
            except Exception:
                pass
