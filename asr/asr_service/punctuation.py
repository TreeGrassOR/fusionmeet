"""Punctuation, true-casing and sentence boundaries for lowercase recogniser text.

The model is 1-800-BAD-CODE/xlm-roberta_punctuation_fullstop_truecase (ONNX, 47
languages, Russian and English included). The inference follows the model card's
"Manual Usage"; the outputs are decoded like ``PunctCapSegResultCollector`` of the
``punctuators`` package (which this service does not install): a mark or a sentence
boundary is attached to a character, so a bare "▁" piece never gets one.

``onnxruntime``, ``sentencepiece`` and ``yaml`` are imported lazily, so the unit
tests of the decoding run without them.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

import numpy as np

from .onnx_engine import cpu_session_options, download_snapshot
from .registry import Punctuator

logger = logging.getLogger(__name__)

WORD_START = "▁"  # SentencePiece's "▁"


@dataclass(frozen=True)
class Labels:
    """Label tables of the model's config.yaml."""

    pre: Sequence[str]
    post: Sequence[str]
    null: str = "<NULL>"
    acronym: str = "<ACRONYM>"


def decode(  # noqa: PLR0913
    pieces: Sequence[str],
    labels: Labels,
    *,
    pre: Sequence[int],
    post: Sequence[int],
    cap: Sequence[Sequence[bool]],
    sbd: Sequence[bool],
) -> str:
    """Rebuild the text of ``pieces`` with the predicted marks, case and sentences.

    Args:
        pieces: SentencePiece pieces, without BOS and EOS.
        labels: the label tables.
        pre: per piece, the index of the mark to put before it (``labels.pre``).
        post: per piece, the index of the mark to put after it (``labels.post``).
        cap: per piece and character, whether the character is upper case.
        sbd: per piece, whether a sentence ends after it.
    """
    sentences: list[str] = []
    current: list[str] = []
    for index, piece in enumerate(pieces):
        if piece.startswith(WORD_START) and current:
            current.append(" ")
        pre_label = labels.pre[pre[index]]
        post_label = labels.post[post[index]]
        first = 1 if piece.startswith(WORD_START) else 0
        last = len(piece) - 1
        upper = cap[index]
        for position in range(first, len(piece)):
            char = piece[position]
            if position == first and pre_label != labels.null:
                current.append(pre_label)
            if position < len(upper) and upper[position]:
                char = char.upper()
            current.append(char)
            if post_label == labels.acronym:
                current.append(".")
            elif position == last and post_label != labels.null:
                current.append(post_label)
            if position == last and sbd[index]:
                sentences.append("".join(current))
                current = []
    if current:
        sentences.append("".join(current))
    return " ".join(sentence.strip() for sentence in sentences if sentence.strip())


def split_ids(ids: Sequence[int], pieces: Sequence[str], limit: int) -> list[list[int]]:
    """Split token ids into runs of at most ``limit``, between words if possible."""
    chunks: list[list[int]] = []
    start = 0
    while len(ids) - start > limit:
        end = start + limit
        # Cut before the last word start of the window, unless that leaves nothing.
        cut = next(
            (i for i in range(end, start, -1) if pieces[i].startswith(WORD_START)),
            end,
        )
        chunks.append(list(ids[start:cut]))
        start = cut
    chunks.append(list(ids[start:]))
    return chunks


class XlmrPunctuator:
    """The ONNX model with its SentencePiece tokenizer. Not thread-safe."""

    def __init__(self, session: Any, tokenizer: Any, labels: Labels, max_length: int):
        """Wrap a loaded session, tokenizer and the config.yaml values."""
        self._session = session
        self._tokenizer = tokenizer
        self._labels = labels
        self._max_length = max_length

    def __call__(self, text: str) -> str:
        """Punctuate and true-case ``text``; "" stays ""."""
        text = text.strip()
        if not text:
            return ""
        tokenizer = self._tokenizer
        ids = tokenizer.EncodeAsIds(text)
        pieces = [tokenizer.IdToPiece(i) for i in ids]
        # The card: longer inputs must be folded over several model inputs.
        chunks = split_ids(ids, pieces, self._max_length - 2)
        return " ".join(part for part in map(self._punctuate, chunks) if part)

    def _punctuate(self, ids: list[int]) -> str:
        tokenizer = self._tokenizer
        batch = [tokenizer.bos_id(), *ids, tokenizer.eos_id()]
        pre, post, cap, sbd = self._session.run(
            None, {"input_ids": np.array([batch], dtype=np.int64)}
        )
        inner = slice(1, len(batch) - 1)
        return decode(
            [tokenizer.IdToPiece(i) for i in ids],
            self._labels,
            pre=pre[0][inner].tolist(),
            post=post[0][inner].tolist(),
            cap=cap[0][inner].tolist(),
            sbd=sbd[0][inner].tolist(),
        )


def build_punctuator(spec: Punctuator, threads: int) -> XlmrPunctuator:
    """Download (or read from ``HF_HOME``), load and warm up the model."""
    import onnxruntime  # noqa: PLC0415  (heavy, imported lazily)
    import yaml  # noqa: PLC0415
    from sentencepiece import SentencePieceProcessor  # noqa: PLC0415

    started = time.monotonic()
    logger.info(
        "Loading %s (revision %s) on cpu, %d thread(s)",
        spec.repo,
        spec.revision,
        threads,
    )
    path = download_snapshot(spec.repo, spec.revision, spec.files)
    with (path / "config.yaml").open(encoding="utf-8") as file:
        config = yaml.safe_load(file)
    labels = Labels(
        pre=config["pre_labels"],
        post=config["post_labels"],
        null=config.get("null_token", "<NULL>"),
        acronym=config.get("acronym_token", "<ACRONYM>"),
    )
    session = onnxruntime.InferenceSession(
        str(path / "model.onnx"),
        sess_options=cpu_session_options(threads),
        providers=["CPUExecutionProvider"],
    )
    tokenizer = SentencePieceProcessor(str(path / "sp.model"))
    punctuator = XlmrPunctuator(session, tokenizer, labels, int(config["max_length"]))
    punctuator("привет")
    logger.info(
        "Loaded %s (revision %s) on cpu in %.1f s",
        spec.key,
        spec.revision,
        time.monotonic() - started,
    )
    return punctuator
