import pytest

from mini_marketo.mailer import ConfigError
from mini_marketo.unsubscribe import UnsubscribeLinks

SECRET = "a" * 40
OTHER = "b" * 40
BASE = "https://marketing.exemplo.pt"


def links(**overrides):
    data = dict(base_url=BASE, secret=SECRET)
    data.update(overrides)
    return UnsubscribeLinks(**data)


def test_token_round_trip_and_url_shape():
    link = links()
    token = link.token_for(42)
    assert token.startswith("42.") and len(token.split(".")[1]) == 22
    assert link.message_id_from(token) == 42
    assert link.url_for(42) == f"{BASE}/unsubscribe/{token}"


def test_a_trailing_slash_in_the_base_url_is_normalized():
    assert links(base_url=BASE + "/").url_for(7).startswith(f"{BASE}/unsubscribe/7.")
    prefixed = links(base_url=BASE + "/app")
    assert prefixed.url_for(7).startswith(f"{BASE}/app/unsubscribe/7.")


def test_tokens_are_deterministic_and_differ_per_message():
    link = links()
    assert link.token_for(5) == link.token_for(5)
    assert link.token_for(5) != link.token_for(6)


def test_a_tampered_signature_or_id_is_rejected():
    link = links()
    token = link.token_for(42)
    message_id, signature = token.split(".")
    flipped = signature[:-1] + ("A" if signature[-1] != "A" else "B")
    assert link.message_id_from(f"{message_id}.{flipped}") is None
    assert link.message_id_from(f"43.{signature}") is None


def test_a_token_from_another_secret_is_rejected():
    assert links().message_id_from(links(secret=OTHER).token_for(42)) is None


@pytest.mark.parametrize(
    "token",
    [
        "",
        None,
        "abc",
        "1",
        "1.",
        "." + "A" * 22,
        "1.short",
        "x." + "A" * 22,
        "0." + "A" * 22,
        "1" * 13 + "." + "A" * 22,
        "1." + "A" * 21 + "!",
        "1." + "A" * 23,
        "-1." + "A" * 22,
    ],
)
def test_malformed_tokens_are_rejected_without_raising(token):
    assert links().message_id_from(token) is None


def test_previous_secrets_still_verify_but_new_links_use_the_current_one():
    old = links(secret=OTHER)
    old_token = old.token_for(9)
    rotated = links(secret=SECRET, previous_secrets=(OTHER,))
    assert rotated.message_id_from(old_token) == 9
    assert rotated.token_for(9) == links().token_for(9)
    assert old.message_id_from(rotated.token_for(9)) is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"base_url": ""},
        {"base_url": "marketing.exemplo.pt"},
        {"base_url": "ftp://marketing.exemplo.pt"},
        {"base_url": "http://marketing.exemplo.pt"},
        {"base_url": BASE + "?x=1"},
        {"base_url": BASE + "#frag"},
        {"secret": "curta"},
        {"previous_secrets": ("curta",)},
    ],
)
def test_invalid_configuration_is_rejected(overrides):
    with pytest.raises(ConfigError):
        links(**overrides)


def test_plain_http_is_accepted_only_for_a_local_server():
    assert links(base_url="http://localhost:8000").url_for(1).startswith("http://localhost:8000/")
    assert links(base_url="http://127.0.0.1:8000").message_id_from(
        links(base_url="http://127.0.0.1:8000").token_for(1)
    ) == 1


def test_secrets_are_not_in_the_repr():
    text = repr(links(previous_secrets=(OTHER,)))
    assert SECRET not in text and OTHER not in text


def test_from_env_reads_the_url_the_key_and_the_previous_keys():
    env = {
        "MINI_MARKETO_PUBLIC_URL": BASE,
        "MINI_MARKETO_SECRET_KEY": SECRET,
        "MINI_MARKETO_SECRET_KEY_PREVIOUS": f" {OTHER} , ,{'c' * 40}",
    }
    link = UnsubscribeLinks.from_env(env)
    assert link.base_url == BASE and link.secret == SECRET
    assert link.previous_secrets == (OTHER, "c" * 40)


def test_from_env_refuses_missing_configuration():
    with pytest.raises(ConfigError):
        UnsubscribeLinks.from_env({})
    with pytest.raises(ConfigError):
        UnsubscribeLinks.from_env({"MINI_MARKETO_PUBLIC_URL": BASE})
    with pytest.raises(ConfigError):
        UnsubscribeLinks.from_env({"MINI_MARKETO_SECRET_KEY": SECRET})
