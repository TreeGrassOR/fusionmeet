"""Tests of the text clean-up rules."""

import pytest

from asr_service.text import clean_text


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("Привет, мир.", "Привет, мир."),
        ("темным круглые сутки<unk>", "темным круглые сутки"),
        ("one <unk> two", "one two"),
        ("<blk><unk>", ""),
        ("a<unk>b", "a b"),
        ("  много   \t пробелов \n", "много пробелов"),
        (".", ""),
        (" . , ! ? — ", ""),
        ("", ""),
        ("Mm-hmm.", "Mm-hmm."),
        ("2 + 2", "2 + 2"),
        ("x < y > z", "x < y > z"),
    ],
)
def test_clean_text(raw, clean):
    """<...> tokens go, whitespace is normalised, punctuation alone is empty."""
    assert clean_text(raw) == clean
