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
