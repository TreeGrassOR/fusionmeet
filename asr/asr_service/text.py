"""Clean-up of recognised text before it is returned."""

from __future__ import annotations

import re

# Special tokens that leak into the text, such as "<unk>".
_TAG = re.compile(r"<[^<>\s]*>")


def clean_text(text: str) -> str:
    """Drop ``<...>`` tokens and extra whitespace; text without a letter or digit is "".

    Punctuation-only output, such as a bare "." on a near-silent clip, is not a
    phrase.
    """
    text = " ".join(_TAG.sub(" ", text).split())
    if not any(char.isalnum() for char in text):
        return ""
    return text
