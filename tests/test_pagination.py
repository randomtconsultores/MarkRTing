import pytest

from mini_marketo.pagination import DEFAULT_PAGE_SIZE, paginate, parse_page, parse_per_page


def test_empty_list_has_one_empty_page():
    page = paginate([], 1, 25)
    assert page.items == [] and page.page == 1 and page.pages == 1 and page.total == 0
    assert (page.first, page.last) == (0, 0)
    assert not page.has_previous and not page.has_next


def test_exact_multiple_has_no_extra_page():
    page = paginate(list(range(50)), 2, 25)
    assert page.pages == 2 and page.items == list(range(25, 50))
    assert (page.first, page.last) == (26, 50)
    assert page.has_previous and not page.has_next


def test_last_page_may_be_incomplete():
    page = paginate(list(range(137)), 6, 25)
    assert page.pages == 6 and len(page.items) == 12
    assert (page.first, page.last) == (126, 137)


def test_middle_page_numbers():
    page = paginate(list(range(137)), 2, 25)
    assert (page.first, page.last) == (26, 50)
    assert page.has_previous and page.has_next


def test_page_beyond_the_end_goes_to_the_last_page():
    assert paginate(list(range(137)), 99, 25).page == 6


@pytest.mark.parametrize("page", [0, -3])
def test_page_below_one_goes_to_the_first_page(page):
    assert paginate(list(range(137)), page, 25).page == 1


def test_order_is_preserved():
    assert paginate(list(range(10)), 2, 4).items == [4, 5, 6, 7]


def test_page_size_must_be_positive():
    with pytest.raises(ValueError):
        paginate([1], 1, 0)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("10", 10),
        (" 50 ", 50),
        ("100", 100),
        ("7", DEFAULT_PAGE_SIZE),
        ("", DEFAULT_PAGE_SIZE),
        ("abc", DEFAULT_PAGE_SIZE),
        ("-5", DEFAULT_PAGE_SIZE),
        (None, DEFAULT_PAGE_SIZE),
    ],
)
def test_parse_per_page_only_accepts_the_listed_sizes(raw, expected):
    assert parse_per_page(raw) == expected


@pytest.mark.parametrize(
    "raw,expected",
    [("3", 3), ("0", 1), ("-2", 1), ("x", 1), ("", 1), (None, 1)],
)
def test_parse_page_never_fails(raw, expected):
    assert parse_page(raw) == expected
