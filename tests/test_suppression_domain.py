import pytest

from mini_marketo.domain.contact import ValidationError
from mini_marketo.domain.suppression import (
    MAX_DETAIL, Suppression, is_removable, validate_suppression,
)
from datetime import datetime


def test_email_and_detail_are_normalized():
    result = validate_suppression("  Ana@A.PT ", "hard_bounce", "smtp", "  550   5.1.1\n user  unknown ")
    assert result == ("ana@a.pt", "hard_bounce", "smtp", "550 5.1.1 user unknown")


def test_detail_is_optional_and_truncated():
    assert validate_suppression("a@a.pt", "complaint", "event", None)[3] == ""
    assert len(validate_suppression("a@a.pt", "complaint", "event", "x" * 900)[3]) == MAX_DETAIL


@pytest.mark.parametrize("email", ["", "sem-arroba", "a@b", None])
def test_invalid_email_is_rejected(email):
    with pytest.raises(ValidationError):
        validate_suppression(email, "manual", "admin")


@pytest.mark.parametrize("reason,source", [("robo", "admin"), ("manual", "robo"), ("", ""), (None, None)])
def test_unknown_reason_or_source_is_rejected(reason, source):
    with pytest.raises(ValidationError):
        validate_suppression("a@a.pt", reason, source)


@pytest.mark.parametrize(
    "reason,source",
    [("manual", "smtp"), ("manual", "event"), ("hard_bounce", "admin"), ("complaint", "admin")],
)
def test_manual_and_admin_only_go_together(reason, source):
    with pytest.raises(ValidationError):
        validate_suppression("a@a.pt", reason, source)


@pytest.mark.parametrize(
    "reason,source",
    [("manual", "admin"), ("hard_bounce", "smtp"), ("hard_bounce", "event"), ("complaint", "event")],
)
def test_valid_combinations_are_accepted(reason, source):
    assert validate_suppression("a@a.pt", reason, source)[1:3] == (reason, source)


def test_only_manual_suppressions_are_removable():
    def make(reason):
        return Suppression(1, "a@a.pt", reason, "admin", "", datetime(2026, 1, 1))

    assert is_removable(make("manual"))
    assert not is_removable(make("hard_bounce"))
    assert not is_removable(make("complaint"))
