import smtplib

import pytest

from mini_marketo.mailer import SendError, classify, is_address_rejection


def refused(code, text):
    return smtplib.SMTPRecipientsRefused({"a@b.pt": (code, text)})


@pytest.mark.parametrize(
    "text",
    [
        b"5.1.1 <a@b.pt>: Recipient address rejected: User unknown",
        b"5.1.2 Bad destination system address",
        b"5.1.3 Bad destination mailbox address syntax",
        b"5.1.6 Mailbox has moved",
        b"5.1.10 Recipient address has null MX",
    ],
)
def test_a_permanent_refusal_about_the_address_is_flagged(text):
    error = classify(refused(550, text))
    assert error.kind == "permanent" and error.address_rejected


@pytest.mark.parametrize(
    "text",
    [
        b"5.7.1 Service unavailable, client host blocked",
        b"5.2.2 Mailbox full",
        b"5.1.7 Bad sender's mailbox address syntax",
        b"5.1.8 Bad sender's system address",
        b"5.1.12 not a known code",
        b"15.1.1 not an enhanced code",
        b"mailbox unavailable",
    ],
)
def test_other_permanent_refusals_are_not_flagged(text):
    error = classify(refused(550, text))
    assert error.kind == "permanent" and not error.address_rejected


def test_a_temporary_refusal_is_never_flagged():
    error = classify(refused(450, b"4.1.1 Mailbox temporarily unavailable"))
    assert error.kind == "temporary" and not error.address_rejected


def test_only_a_recipient_refusal_can_flag_an_address():
    error = classify(smtplib.SMTPResponseException(550, b"5.1.1 User unknown"))
    assert error.kind == "permanent" and not error.address_rejected


def test_send_error_is_not_flagged_by_default():
    assert not SendError("permanent", "550 no such user").address_rejected
    assert is_address_rejection("550 5.1.1 user unknown")
