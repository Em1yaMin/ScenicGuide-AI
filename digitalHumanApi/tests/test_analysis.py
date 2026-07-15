import pytest

from app.tasks.analysis import _normalize_string_list


def test_normalize_string_list_strips_and_dedupes():
    assert _normalize_string_list([" 故宫 ", "故宫", 123, ""]) == ["故宫"]


def test_normalize_string_list_returns_none_for_non_list():
    assert _normalize_string_list("not a list") is None


def test_normalize_string_list_returns_none_for_all_empty():
    assert _normalize_string_list(["", "  "]) is None
