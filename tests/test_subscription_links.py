import pytest

from mini_marketo.subscription_links import SubscriptionLinks
from mini_marketo.unsubscribe import UnsubscribeLinks

BASE = "https://marketing.exemplo.pt"
KEY = "k" * 40


def links(secret=KEY, previous=()):
    return SubscriptionLinks(UnsubscribeLinks(BASE, secret, previous))


def test_a_token_round_trips_and_the_url_points_to_the_confirmation_page():
    token = links().token_for(7)
    assert links().request_id_from(token) == 7
    assert links().url_for(7) == f"{BASE}/subscribe/confirm/{token}"


def test_tampered_or_malformed_tokens_are_rejected():
    signature = links().token_for(7).split(".")[1]
    for token in (
        None, "", "x", "7", "7.", "7.abc", f"0.{signature}", f"8.{signature}",
        f"7.{signature[:-1]}A", f"-7.{signature}", f"7.{signature}.extra", f" 7.{signature}",
    ):
        assert links().request_id_from(token) is None, token


def test_a_different_key_is_rejected_and_a_previous_key_is_accepted():
    old = "o" * 40
    token = links(old).token_for(7)
    assert links().request_id_from(token) is None
    assert links(KEY, (old,)).request_id_from(token) == 7


def test_confirmation_and_unsubscribe_tokens_are_not_interchangeable():
    unsubscribe = UnsubscribeLinks(BASE, KEY)
    assert links().request_id_from(unsubscribe.token_for(7)) is None
    assert unsubscribe.message_id_from(links().token_for(7)) is None
