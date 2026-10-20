import asyncio

import pytest

from mini_marketo.login_form import LoginFormError, read_login_form

TYPE = "application/x-www-form-urlencoded"


def messages(*chunks):
    return [{"type": "http.request", "body": chunk, "more_body": i < len(chunks) - 1}
            for i, chunk in enumerate(chunks)]


def read(events, content_type=TYPE, *, max_bytes=128, max_fields=4):
    async def receive():
        assert events, "Leitura inesperada depois do fim do corpo."
        return events.pop(0)
    return asyncio.run(read_login_form(receive, content_type,
                       max_bytes=max_bytes, max_fields=max_fields))


def test_chunked_utf8_and_password_are_preserved():
    result = read(messages(b"username=jo%C3", b"%A3o&password=++a%2Bb++&csrf_token="))
    assert result == {"username": "joão", "password": "  a+b  ", "csrf_token": ""}


def test_empty_body_is_parsed_but_does_not_validate_required_fields():
    assert read(messages(b"")) == {}


@pytest.mark.parametrize("value", ["application/x-www-form-urlencoded; charset=UTF-8",
                                   'application/x-www-form-urlencoded;charset="utf-8"'])
def test_explicit_utf8_content_type(value):
    assert read(messages(b"a=b"), value) == {"a": "b"}


@pytest.mark.parametrize("body", [b"a=%", b"a=%A", b"a=%GG", b"a=%FF", b"username", b"=secret"])
def test_malformed_form_does_not_echo_body(body):
    with pytest.raises(LoginFormError) as error:
        read(messages(body))
    assert error.value.status_code == 400
    assert body.decode() not in str(error.value)
    assert error.value.__suppress_context__


@pytest.mark.parametrize("body", [b"username=a&username=b", b"username=a&%75sername=b"])
def test_duplicate_decoded_names_are_rejected(body):
    with pytest.raises(LoginFormError) as error:
        read(messages(body))
    assert error.value.status_code == 400


@pytest.mark.parametrize("value", [None, "application/json", "multipart/form-data; boundary=x",
                                   TYPE + "; charset=latin-1", TYPE + "\r\n"])
def test_unsupported_content_type_does_not_read(value):
    events = messages(b"secret")
    with pytest.raises(LoginFormError) as error:
        read(events, value)
    assert error.value.status_code == 415
    assert len(events) == 1


def test_exact_byte_limit_and_streaming_overflow():
    assert read(messages(b"a=123"), max_bytes=5) == {"a": "123"}
    events = messages(b"a=12", b"34", b"unread")
    with pytest.raises(LoginFormError) as error:
        read(events, max_bytes=5)
    assert error.value.status_code == 413
    assert len(events) == 1


def test_field_count_is_bounded():
    with pytest.raises(LoginFormError) as error:
        read(messages(b"a=1&b=2"), max_fields=1)
    assert error.value.status_code == 400


def test_disconnect_before_complete_body_is_rejected():
    with pytest.raises(LoginFormError) as error:
        read([{"type": "http.request", "body": b"a=1", "more_body": True},
              {"type": "http.disconnect"}])
    assert error.value.status_code == 400


@pytest.mark.parametrize("options", [{"max_bytes": 0}, {"max_bytes": True},
                                    {"max_fields": -1}, {"max_fields": 1.5}])
def test_invalid_limits_fail_before_reading(options):
    events = messages(b"a=1")
    with pytest.raises(ValueError):
        read(events, **options)
    assert len(events) == 1
