import pytest

from mini_marketo.domain.campaign import MAX_HTML_LENGTH, MAX_TEXT_LENGTH, Campaign, render
from mini_marketo.domain.contact import Contact, ValidationError


def make(**overrides):
    data = dict(
        id=None,
        name="Outubro",
        subject="Olá {first_name}",
        body_text="Texto",
        body_html="",
        purpose="newsletter",
        segment_id=1,
        segment_name="Todos",
    )
    data.update(overrides)
    return Campaign(**data)


def test_valid_campaign_is_normalized():
    c = make(name="  Outubro  ", subject="  Olá   mundo ", body_text="  Texto  ", body_html=" <p>x</p> ")
    assert (c.name, c.subject, c.body_text, c.body_html) == ("Outubro", "Olá mundo", "Texto", "<p>x</p>")
    assert c.status == "draft" and c.created_at is None and c.queued_at is None


def test_subject_cannot_carry_line_breaks_or_extra_headers():
    c = make(subject="Olá\r\nBcc: x@y.z")
    assert c.subject == "Olá Bcc: x@y.z"
    assert "\n" not in c.subject and "\r" not in c.subject


@pytest.mark.parametrize(
    "overrides",
    [
        {"name": ""},
        {"subject": "   "},
        {"subject": "x" * 201},
        {"body_text": "  "},
        {"body_text": "x" * (MAX_TEXT_LENGTH + 1)},
        {"body_html": "x" * (MAX_HTML_LENGTH + 1)},
        {"purpose": "outra"},
        {"status": "enviada"},
        {"segment_name": "  "},
    ],
)
def test_invalid_campaigns_are_rejected(overrides):
    with pytest.raises(ValidationError):
        make(**overrides)


def test_render_replaces_only_the_known_placeholders():
    contact = Contact(1, "Ana", "Silva", ("ana@a.pt",))
    rendered = render(make(subject="Olá {first_name}", body_text="{first_name} {last_name} {outro}"), contact)
    assert rendered.subject == "Olá Ana"
    assert rendered.text == "Ana Silva {outro}"
    assert rendered.html is None


def test_render_escapes_values_in_html_but_not_in_text():
    contact = Contact(1, "<b>Ana</b>", "O'Neil", ("ana@a.pt",))
    rendered = render(make(body_text="{first_name} {last_name}", body_html="<p>{first_name} {last_name}</p>"), contact)
    assert rendered.text == "<b>Ana</b> O'Neil"
    assert rendered.html == "<p>&lt;b&gt;Ana&lt;/b&gt; O&#x27;Neil</p>"


def test_render_does_not_interpret_template_syntax_in_names():
    contact = Contact(1, "{last_name}", "{{ 7*7 }}", ("ana@a.pt",))
    rendered = render(make(body_text="{first_name}|{last_name}"), contact)
    assert rendered.text == "{last_name}|{{ 7*7 }}"


URL = "https://marketing.exemplo.pt/unsubscribe/1.abc"


def test_render_without_a_link_adds_no_footer():
    contact = Contact(1, "Ana", "Silva", ("ana@a.pt",))
    rendered = render(make(body_html="<p>x</p>"), contact)
    assert URL not in rendered.text and rendered.html == "<p>x</p>"


def test_render_adds_the_unsubscribe_link_to_text_and_html():
    contact = Contact(1, "Ana", "Silva", ("ana@a.pt",))
    rendered = render(make(body_text="Texto", body_html="<p>x</p>"), contact, unsubscribe_url=URL)
    assert rendered.text.startswith("Texto") and rendered.text.rstrip().endswith(URL)
    assert f'href="{URL}"' in rendered.html and rendered.html.startswith("<p>x</p>")


def test_a_text_only_campaign_stays_text_only_with_the_link():
    contact = Contact(1, "Ana", "Silva", ("ana@a.pt",))
    rendered = render(make(), contact, unsubscribe_url=URL)
    assert URL in rendered.text and rendered.html is None


def test_the_link_in_the_html_is_escaped():
    contact = Contact(1, "Ana", "Silva", ("ana@a.pt",))
    rendered = render(make(body_html="<p>x</p>"), contact, unsubscribe_url='https://a.pt/"><b>')
    assert '"><b>' not in rendered.html and "&quot;&gt;&lt;b&gt;" in rendered.html
