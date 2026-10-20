import pytest
from app.draft_review import build_whatsapp_url, missing_template_fields, render_review_message, validate_template


def test_template_accepts_only_known_fields():
    assert validate_template("Oi {nome}, empresa {empresa}") == {"nome", "empresa"}
    with pytest.raises(ValueError):
        validate_template("Oi {token}")


def test_render_review_message_personalizes_fields():
    lead = {"nome": "Ana", "empresa": "Exemplo Ltda", "nicho": "serviços", "cidade": "Guarulhos"}
    assert render_review_message("Oi {nome}, falo da Visível sobre {empresa}.", lead) == "Oi Ana, falo da Visível sobre Exemplo Ltda."


def test_missing_personalization_fields_are_detected():
    lead = {"nome": "", "empresa": "ABC", "nicho": "", "cidade": ""}
    assert missing_template_fields("Olá {nome} da {empresa}", lead) == ["nome"]
    with pytest.raises(ValueError):
        render_review_message("Olá {nome} da {empresa}", lead)


def test_build_whatsapp_url_encodes_message_and_never_sends():
    url = build_whatsapp_url("(11) 99999-9999", "Olá Ana! Teste & revisão")
    assert url.startswith("https://web.whatsapp.com/send?")
    assert "phone=5511999999999" in url
    assert "text=Ol%C3%A1" in url
    assert "revis%C3%A3o" in url


def test_message_template_length_is_limited():
    with pytest.raises(ValueError):
        validate_template("x" * 4001)
