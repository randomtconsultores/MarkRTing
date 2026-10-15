import pytest

from mini_marketo import texts
from mini_marketo.domain.consent import PURPOSES


def test_the_default_language_exists_and_is_listed():
    assert "pt" in texts.available_languages()


def test_every_language_has_the_same_keys_and_placeholders():
    assert texts.catalog_problems(texts.load_catalog()) == []


def test_the_check_detects_missing_extra_and_different_placeholders():
    base = {"a": "Olá {name}", "b": "x"}
    assert texts.catalog_problems({"pt": base, "en": dict(base)}) == []
    problems = texts.catalog_problems({"pt": base, "en": {"a": "Hello", "c": "z"}})
    assert "en: falta a chave b." in problems
    assert "en: chave a mais c." in problems
    assert "en: a usa marcadores diferentes." in problems
    assert texts.catalog_problems({"en": base}) == ["Falta o idioma por omissão (pt)."]


def test_every_purpose_has_a_name_in_every_language():
    for lang, table in texts.load_catalog().items():
        for key in PURPOSES:
            assert f"purpose.{key}" in table, f"{lang}: falta purpose.{key}"


def test_no_text_is_empty_or_malformed():
    for lang, table in texts.load_catalog().items():
        for key, value in table.items():
            assert value.strip(), f"{lang}: {key} está vazio"
            texts.placeholders(value)  # levanta ValueError se as chavetas estiverem mal


def test_a_text_is_filled_in_and_falls_back_to_the_default_language():
    assert "48 horas" in texts.text("pt", "received.body", hours=48)
    assert texts.text("xx", "received.body", hours=48) == texts.text("pt", "received.body", hours=48)


def test_an_unknown_key_or_a_missing_parameter_is_a_programming_error():
    with pytest.raises(KeyError):
        texts.text("pt", "nao.existe")
    with pytest.raises(KeyError):
        texts.text("pt", "received.body")


def test_the_confirmation_email_fills_in_all_its_placeholders():
    body = texts.text(
        "pt", "confirm_email.text", first_name="Ana", purposes="Newsletter", hours=48,
        url="https://x.pt/subscribe/1.abc", privacy="",
    )
    assert "Ana" in body and "https://x.pt/subscribe/1.abc" in body and "48 horas" in body
