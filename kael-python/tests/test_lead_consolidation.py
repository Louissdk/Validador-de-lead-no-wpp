import json

from app.lead_consolidation import build_consolidation_preview


HEADERS = ["position", "title", "address", "rating", "type", "phoneNumber", "website", "email"]


def test_preview_preserves_source_coverage_and_candidate_reference():
    values = [
        HEADERS,
        ["1", "Beco Pizzaria", "Rua A, 10", "4.5", "Pizzaria", "(11) 98888-7777", "", ""],
        ["2", "BÉCO PIZZARIA", "Rua A, 10", "4.5", "Pizzaria", "11988887777", "https://beco.example", "contato@beco.example"],
        ["3", "Mercado Central", "Rua B, 20", "4.1", "Mercado", "11988887777", "", ""],
        ["4", "Sem Endereco", "", "4.0", "Loja", "11999990000", "", ""],
    ]

    matrix, stats = build_consolidation_preview(values)
    headers = matrix[0]
    rows = [dict(zip(headers, row)) for row in matrix[1:]]

    assert stats["linhas_origem"] == 4
    assert stats["linhas_origem_referenciadas"] == 4
    assert stats["grupos_identidade"] == 3
    assert stats["linhas_previa"] == 3  # the duplicate candidate collapses; shared-phone identities remain separate
    assert stats["grupos_com_multiplos_telefones"] == 0
    assert stats["telefones_compartilhados_distintos"] == 1

    pizza = next(row for row in rows if row["grupo_nome_endereco"] and row["quantidade_registros_origem"] == "2")
    assert pizza["status_consolidacao_v5"] == "DUPLICATA_CANDIDATA"
    assert pizza["linhas_origem_revalidacao"] == "2,3"
    assert pizza["website"] == "https://beco.example"  # empty field filled from a unique variant
    assert pizza["status_whatsapp_auditoria"] == "NAO_VERIFICADO"


def test_multiple_different_phones_in_same_identity_are_kept_as_separate_rows():
    values = [
        HEADERS,
        ["1", "Oficina Alfa", "Av. Brasil, 100", "4.4", "Oficina", "11988887777", "", ""],
        ["2", "Oficina Alfa", "Av. Brasil, 100", "4.4", "Oficina", "11977776666", "", ""],
    ]
    matrix, stats = build_consolidation_preview(values)
    headers = matrix[0]
    rows = [dict(zip(headers, row)) for row in matrix[1:]]

    assert stats["grupos_identidade"] == 1
    assert stats["linhas_previa"] == 2
    assert stats["grupos_com_multiplos_telefones"] == 1
    assert {row["status_consolidacao_v5"] for row in rows} == {"REVISAR_MULTIPLOS_TELEFONES"}
    assert {row["grupo_nome_endereco"] for row in rows} == {"NA-000001"}
    assert {row["linhas_origem_revalidacao"] for row in rows} == {"2,3"}


def test_rows_missing_name_or_address_are_never_merged():
    values = [
        HEADERS,
        ["1", "", "Rua A, 1", "4.2", "Loja", "11988887777", "", ""],
        ["2", "", "Rua A, 1", "4.2", "Loja", "11988887777", "", ""],
        ["3", "Loja Beta", "", "4.2", "Loja", "11988887777", "", ""],
    ]
    matrix, stats = build_consolidation_preview(values)
    headers = matrix[0]
    rows = [dict(zip(headers, row)) for row in matrix[1:]]

    assert stats["linhas_previa"] == 3
    assert stats["linhas_origem_referenciadas"] == 3
    assert all(row["status_consolidacao_v5"] == "IDENTIDADE_INCOMPLETA_REVISAR" for row in rows)


def test_distinct_valid_and_invalid_phones_are_not_silently_collapsed():
    values = [
        HEADERS,
        ["1", "Clínica Delta", "Rua C, 30", "4.3", "Clínica", "11988887777", "", ""],
        ["2", "Clinica Delta", "Rua C, 30", "4.3", "Clínica", "123", "", ""],
    ]
    matrix, stats = build_consolidation_preview(values)
    headers = matrix[0]
    rows = [dict(zip(headers, row)) for row in matrix[1:]]

    assert stats["grupos_identidade"] == 1
    assert stats["linhas_previa"] == 2
    assert stats["grupos_com_multiplos_telefones"] == 1
    assert all(row["status_consolidacao_v5"] == "REVISAR_MULTIPLOS_TELEFONES" for row in rows)

def test_non_alias_original_field_variants_are_preserved_and_flagged():
    headers = [
        "position", "title", "address", "rating", "type",
        "phoneNumber", "website", "email", "hours", "description",
    ]
    values = [
        headers,
        ["1", "Oficina Alfa", "Rua Brasil, 10", "4.5", "Oficina",
         "11988887777", "", "", "Seg-Sex", "Atende industrial"],
        ["2", "OFICINA ALFA", "Rua Brasil, 10", "4.5", "Oficina",
         "11988887777", "", "", "Sabados", "Atende varejo"],
    ]
    matrix, stats = build_consolidation_preview(values)
    output = [dict(zip(matrix[0], row)) for row in matrix[1:]]
    assert len(output) == 1
    row = output[0]
    assert row["status_consolidacao_v5"] == "CANDIDATO_COM_CONFLITOS"
    assert row["linhas_origem_revalidacao"] == "2,3"
    assert row["hours"] == "Seg-Sex"
    assert row["description"] == "Atende industrial"
    assert "hours" in row["campos_originais_com_variantes"]
    assert "description" in row["campos_originais_com_variantes"]
    variants = json.loads(row["variantes_campos_originais"])
    assert variants["hours"] == ["Seg-Sex", "Sabados"]
    assert variants["description"] == ["Atende industrial", "Atende varejo"]
    assert variants["title"] == ["Oficina Alfa", "OFICINA ALFA"]
    assert stats["grupos_com_conflitos"] == 1
    assert stats["conflitos_por_campo"]["hours"] == 1
    assert stats["conflitos_por_campo"]["description"] == 1


def test_category_and_rating_variants_do_not_escalate_identity_conflict_status():
    import json
    from app.lead_consolidation import build_consolidation_preview

    headers = [
        "position", "title", "address", "rating", "type",
        "phoneNumber", "website", "email", "hours", "description",
    ]
    values = [
        headers,
        ["1", "Oficina Beta", "Rua Brasil, 10", "4.2", "Oficina",
         "11988887777", "", "", "Seg-Sex", "Manutenção automotiva"],
        ["2", "OFICINA BETA", "Rua Brasil, 10", "4.8", "Centro automotivo",
         "11988887777", "", "", "Seg-Sex", "Manutenção automotiva"],
    ]

    matrix, stats = build_consolidation_preview(values)
    output = [dict(zip(matrix[0], row)) for row in matrix[1:]]

    assert len(output) == 1
    row = output[0]
    assert row["status_consolidacao_v5"] == "DUPLICATA_CANDIDATA"
    variants = json.loads(row["variantes_campos_originais"])
    assert variants["type"] == ["Oficina", "Centro automotivo"]
    assert variants["rating"] == ["4.2", "4.8"]
    assert stats["grupos_com_conflitos"] == 1
    assert stats["conflitos_por_campo"]["segmento"] == 1
    assert stats["conflitos_por_campo"]["avaliacao"] == 1


def test_site_and_email_variants_still_require_conflict_review():
    from app.lead_consolidation import build_consolidation_preview

    headers = [
        "position", "title", "address", "rating", "type",
        "phoneNumber", "website", "email", "hours", "description",
    ]
    base_a = [
        "1", "Consultório Gama", "Rua A, 10", "4.5", "Clínica",
        "11988887777", "https://gama.example", "contato@gama.example",
        "Seg-Sex", "Atendimento clínico",
    ]
    base_b = list(base_a)
    base_b[0] = "2"

    for field, index, value in (
        ("site", 6, "https://gama-saude.example"),
        ("email", 7, "agenda@gama.example"),
    ):
        candidate = list(base_b)
        candidate[index] = value
        matrix, _stats = build_consolidation_preview([headers, base_a, candidate])
        output = [dict(zip(matrix[0], row)) for row in matrix[1:]]
        assert len(output) == 1
        assert output[0]["status_consolidacao_v5"] == "CANDIDATO_COM_CONFLITOS", field

