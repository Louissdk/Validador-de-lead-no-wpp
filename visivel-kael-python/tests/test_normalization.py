import pytest
from app.normalization import normalize_phone, normalize_consent, render_template


@pytest.mark.parametrize(("value", "expected"), [
    ("11987654321", "5511987654321"),
    ("(11) 98765-4321", "5511987654321"),
    ("+55 11 98765-4321", "5511987654321"),
    ("", ""),
    ("123", ""),
])
def test_normalize_phone(value, expected):
    assert normalize_phone(value) == expected


def test_opt_in_requires_source_and_timestamp():
    assert normalize_consent("opt_in", "", "") == ("unknown", None, None)
    assert normalize_consent("opt_in", "formulario", "2026-10-01") == ("opt_in", "formulario", "2026-10-01")


def test_template_only_renders_known_fields():
    assert render_template("Olá {nome} da {empresa}", {"nome": "Ana", "empresa": "ABC"}) == "Olá Ana da ABC"
    with pytest.raises(ValueError):
        render_template("Olá {token}", {})
