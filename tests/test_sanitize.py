"""Unit tests for shared/sanitize.py."""
from shared.sanitize import sanitize_external_text


def test_none_and_empty_return_empty_string():
    assert sanitize_external_text(None) == ""
    assert sanitize_external_text("") == ""


def test_strips_html_tags():
    assert sanitize_external_text("<b>Bold</b> text") == "Bold text"


def test_strips_control_characters():
    assert sanitize_external_text("hello\x00\x07world") == "helloworld"


def test_collapses_whitespace():
    assert sanitize_external_text("a   b\n\nc\td") == "a b c d"


def test_truncates_to_max_len():
    text = "x" * 500
    assert len(sanitize_external_text(text, max_len=50)) == 50


def test_preserves_normal_text():
    assert sanitize_external_text("Apple reports record earnings.") == "Apple reports record earnings."
