from app.sheets_planner import OUTPUT_HEADERS, plan_lead, recommend_product, classify_niche


def base_lead(**changes):
    row = {
        "numero": "5511999999999",
        "nome": "Empresa Exemplo",
        "nicho": "pizzaria",
        "nicho_group": "food",
        "endereco": "Guarulhos",
        "rating": "4.7",
        "tem_site": "false",
        "score": "90",
        "priority_level": "quente",
    }
    row.update(changes)
    return row


def test_public_lead_without_opt_in_gets_analysis_but_no_whatsapp_message():
    planned = plan_lead(base_lead())
    assert planned["produto_recomendado"] == "Cardápio digital / site com informações de atendimento"
    assert planned["prioridade_comercial"] == "quente"
    assert planned["mensagem_completa_copiar_colar"] == ""
    assert planned["status_revisao"] == "AGUARDAR_AUTORIZACAO"


def test_opt_in_with_documented_source_generates_paragraphs():
    planned = plan_lead(base_lead(
        consent_status="opt_in",
        consent_source="formulario_site",
        consent_at="2026-10-15",
    ))
    assert planned["mensagem_completa_copiar_colar"]
    assert "\n\n" in planned["mensagem_completa_copiar_colar"]
    assert planned["paragrafo_1_abertura"].startswith("Olá, Empresa Exemplo")
    assert planned["status_revisao"] == "PENDENTE_REVISAO"


def test_opt_out_wins_over_existing_opt_in():
    planned = plan_lead(base_lead(
        consent_status="opt_out",
        consent_source="formulario_site",
        consent_at="2026-10-15",
    ))
    assert planned["status_revisao"] == "BLOQUEADO_DESCADASTRO"
    assert not planned["mensagem_completa_copiar_colar"]


def test_reply_registered_blocks_message():
    planned = plan_lead(base_lead(resposta_recebida="sim", consent_status="opt_in", consent_source="form", consent_at="2026-10-15"))
    assert planned["status_revisao"] == "BLOQUEADO_JA_RESPONDEU"
    assert not planned["mensagem_completa_copiar_colar"]


def test_site_unknown_does_not_claim_site_absence():
    planned = plan_lead(base_lead(tem_site=""))
    assert planned["perfil_lead"] == "Presença digital não verificada"
    assert "Diagnóstico de presença digital" in planned["produto_recomendado"]


def test_bad_phone_is_rejected():
    import pytest
    with pytest.raises(ValueError):
        plan_lead(base_lead(numero="123"))


def test_niche_categories_are_normalized():
    assert classify_niche("Clínica odontológica") == "saude"
    assert classify_niche("Barbearia masculina") == "beleza"
    assert classify_niche("Loja de roupas") == "varejo"


def test_output_contains_copy_paste_and_breakdown_columns():
    assert "mensagem_completa_copiar_colar" in OUTPUT_HEADERS
    assert "paragrafo_2_contexto" in OUTPUT_HEADERS


def test_existing_output_opt_out_is_sticky_on_rerun():
    from app.sheets_planner import _rows_preserving_review

    fresh = plan_lead(base_lead(
        consent_status="opt_in",
        consent_source="formulario",
        consent_at="2026-10-15",
    ))
    old = [{
        "telefone": fresh["telefone"],
        "status_autorizacao_whatsapp": "opt_out",
        "status_revisao": "BLOQUEADO_DESCADASTRO",
        "mensagem_completa_copiar_colar": "texto antigo que nao deve voltar",
    }]
    merged = _rows_preserving_review(old, [fresh])
    assert merged[0]["status_autorizacao_whatsapp"] == "opt_out"
    assert merged[0]["status_revisao"] == "BLOQUEADO_DESCADASTRO"
    assert merged[0]["mensagem_completa_copiar_colar"] == ""


def test_existing_manual_message_is_preserved_when_still_eligible():
    from app.sheets_planner import _rows_preserving_review

    fresh = plan_lead(base_lead(
        consent_status="opt_in",
        consent_source="formulario",
        consent_at="2026-10-15",
    ))
    old = [{
        "telefone": fresh["telefone"],
        "status_autorizacao_whatsapp": "opt_in",
        "status_revisao": "APROVADO_MANUALMENTE",
        "mensagem_completa_copiar_colar": "texto editado pelo Luis",
        "nota_revisor": "revisado",
    }]
    merged = _rows_preserving_review(old, [fresh])
    assert merged[0]["mensagem_completa_copiar_colar"] == "texto editado pelo Luis"
    assert merged[0]["status_revisao"] == "APROVADO_MANUALMENTE"


def test_consolidated_source_phone_alias_and_website_are_recognized():
    planned = plan_lead({
        "telefone_normalizado": "5511999999999",
        "title": "Empresa Exemplo",
        "type": "pizzaria",
        "address": "Guarulhos",
        "website": "https://exemplo.com.br",
    })
    assert planned["telefone"] == "5511999999999"
    assert planned["site_identificado_na_fonte"] == "Sim"
    assert "Diagnóstico do cardápio digital" in planned["produto_recomendado"]


def test_shared_phone_does_not_transfer_manual_message_between_businesses():
    from app.sheets_planner import _rows_preserving_review

    first = plan_lead(base_lead(nome="Oficina Alfa", endereco="Rua A, 10", consent_status="opt_in", consent_source="form", consent_at="2026-10-15"))
    second = plan_lead(base_lead(nome="Oficina Beta", endereco="Rua B, 20", consent_status="opt_in", consent_source="form", consent_at="2026-10-15"))
    old = [{
        "telefone": first["telefone"],
        "nome": "Oficina Alfa",
        "endereco": "Rua A, 10",
        "status_autorizacao_whatsapp": "opt_in",
        "status_revisao": "APROVADO_MANUALMENTE",
        "mensagem_completa_copiar_colar": "MENSAGEM PRIVADA DA OFICINA ALFA",
    }]

    merged = _rows_preserving_review(old, [first, second])
    by_name = {row["nome"]: row for row in merged}
    assert by_name["Oficina Alfa"]["mensagem_completa_copiar_colar"] == "MENSAGEM PRIVADA DA OFICINA ALFA"
    assert by_name["Oficina Beta"]["mensagem_completa_copiar_colar"] != "MENSAGEM PRIVADA DA OFICINA ALFA"


def test_phone_wide_opt_out_blocks_all_businesses_sharing_phone():
    from app.sheets_planner import _rows_preserving_review

    first = plan_lead(base_lead(nome="Oficina Alfa", endereco="Rua A, 10", consent_status="opt_in", consent_source="form", consent_at="2026-10-15"))
    second = plan_lead(base_lead(nome="Oficina Beta", endereco="Rua B, 20", consent_status="opt_in", consent_source="form", consent_at="2026-10-15"))
    old = [{
        "telefone": first["telefone"],
        "nome": "Oficina Alfa",
        "endereco": "Rua A, 10",
        "status_autorizacao_whatsapp": "opt_out",
        "status_revisao": "BLOQUEADO_DESCADASTRO",
    }]

    merged = _rows_preserving_review(old, [first, second])
    assert all(row["status_autorizacao_whatsapp"] == "opt_out" for row in merged)
    assert all(not row["mensagem_completa_copiar_colar"] for row in merged)

# KAEL orphan review retention regression tests v1
def test_ambiguous_incomplete_legacy_rows_without_review_history_are_not_carried_forward():
    from app.sheets_planner import _rows_preserving_review
    fresh = plan_lead(base_lead(nome="Empresa Atual", endereco="Rua Atual, 10"))
    phone = fresh["telefone"]
    old = [
        {
            "telefone": phone,
            "nome": "Empresa Legada A",
            "status_autorizacao_whatsapp": "unknown",
            "status_revisao": "REVISAR_CONSOLIDACAO",
        },
        {
            "telefone": phone,
            "nome": "Empresa Legada B",
            "status_autorizacao_whatsapp": "unknown",
            "status_revisao": "REVISAR_CONSOLIDACAO",
        },
    ]
    merged = _rows_preserving_review(old, [fresh])
    assert len(merged) == 1
    assert merged[0]["nome"] == fresh["nome"]
    assert {row.get("nome") for row in merged} == {fresh["nome"]}
def test_ambiguous_incomplete_legacy_row_with_reviewer_note_is_preserved():
    from app.sheets_planner import _rows_preserving_review
    fresh = plan_lead(base_lead(nome="Empresa Atual", endereco="Rua Atual, 10"))
    phone = fresh["telefone"]
    old = [
        {
            "telefone": phone,
            "nome": "Empresa Legada A",
            "status_autorizacao_whatsapp": "unknown",
            "status_revisao": "REVISAR_CONSOLIDACAO",
        },
        {
            "telefone": phone,
            "nome": "Empresa Legada B",
            "status_autorizacao_whatsapp": "unknown",
            "status_revisao": "REVISAR_CONSOLIDACAO",
            "nota_revisor": "Conferir identidade antes de arquivar.",
        },
    ]
    merged = _rows_preserving_review(old, [fresh])
    assert any(
        row.get("nota_revisor") == "Conferir identidade antes de arquivar."
        for row in merged
    )
def test_incomplete_legacy_opt_out_remains_preserved_and_phone_wide():
    from app.sheets_planner import _rows_preserving_review
    fresh = plan_lead(base_lead(
        nome="Empresa Atual",
        endereco="Rua Atual, 10",
        consent_status="opt_in",
        consent_source="formulario",
        consent_at="2026-10-15",
    ))
    phone = fresh["telefone"]
    old = [
        {
            "telefone": phone,
            "nome": "Empresa Legada A",
            "status_autorizacao_whatsapp": "opt_out",
            "status_revisao": "BLOQUEADO_DESCADASTRO",
        },
        {
            "telefone": phone,
            "nome": "Empresa Legada B",
            "status_autorizacao_whatsapp": "unknown",
            "status_revisao": "REVISAR_CONSOLIDACAO",
        },
    ]
    merged = _rows_preserving_review(old, [fresh])
    assert merged[0]["status_autorizacao_whatsapp"] == "opt_out"
    assert not merged[0]["mensagem_completa_copiar_colar"]
    assert any(
        row.get("status_autorizacao_whatsapp") == "opt_out"
        and row.get("status_revisao") == "BLOQUEADO_DESCADASTRO"
        for row in merged[1:]
    )

# KAEL orphan blocked message regression v1
def test_incomplete_legacy_opt_out_preserves_note_but_clears_old_message():
    from app.sheets_planner import _rows_preserving_review
    fresh = plan_lead(base_lead(
        nome="Empresa Atual",
        endereco="Rua Atual, 10",
        consent_status="opt_in",
        consent_source="formulario",
        consent_at="2026-10-15",
    ))
    phone = fresh["telefone"]
    old = [
        {
            "telefone": phone,
            "nome": "Empresa Legada A",
            "status_autorizacao_whatsapp": "opt_out",
            "status_revisao": "BLOQUEADO_DESCADASTRO",
            "mensagem_completa_copiar_colar": "TEXTO ANTIGO NAO PODE SER REUTILIZADO",
            "paragrafo_1_abertura": "ABERTURA ANTIGA",
            "nota_revisor": "Descadastro registrado; manter bloqueio.",
        },
        {
            "telefone": phone,
            "nome": "Empresa Legada B",
            "status_autorizacao_whatsapp": "unknown",
            "status_revisao": "REVISAR_CONSOLIDACAO",
        },
    ]
    merged = _rows_preserving_review(old, [fresh])
    assert merged
    assert all(row["status_autorizacao_whatsapp"] == "opt_out" for row in merged)
    assert all(not row.get("mensagem_completa_copiar_colar") for row in merged)
    assert all(not row.get("paragrafo_1_abertura") for row in merged)
    assert any(
        row.get("nota_revisor") == "Descadastro registrado; manter bloqueio."
        for row in merged
    )
    assert any(
        row.get("status_revisao") == "BLOQUEADO_DESCADASTRO"
        for row in merged
    )

# KAEL orphan generated-draft retention regression v1
def test_generated_internal_draft_alone_does_not_retain_ambiguous_incomplete_legacy_rows():
    from app.sheets_planner import _rows_preserving_review
    fresh = plan_lead(
        base_lead(nome="Empresa Atual", endereco="Rua Atual, 10")
    )
    phone = fresh["telefone"]
    old = [
        {
            "telefone": phone,
            "nome": "Empresa Legada A",
            "status_autorizacao_whatsapp": "unknown",
            "status_revisao": "REVISAR_CONSOLIDACAO",
            "rascunho_comercial_interno": "Rascunho criado automaticamente.",
        },
        {
            "telefone": phone,
            "nome": "Empresa Legada B",
            "status_autorizacao_whatsapp": "unknown",
            "status_revisao": "REVISAR_CONSOLIDACAO",
            "rascunho_comercial_interno": "Outro rascunho automatico.",
        },
    ]
    merged = _rows_preserving_review(old, [fresh])
    assert len(merged) == 1
    assert merged[0]["nome"] == fresh["nome"]
