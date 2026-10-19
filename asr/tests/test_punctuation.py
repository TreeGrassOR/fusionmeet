"""Tests of the punctuation post-processor without onnxruntime or sentencepiece."""

import numpy as np

from asr_service.punctuation import Labels, XlmrPunctuator, decode, split_ids

PRE = ["<NULL>", "¿"]
POST = ["<NULL>", "<ACRONYM>", ".", ",", "?"]
LABELS = Labels(pre=PRE, post=POST)
NULL, ACRONYM, DOT, COMMA, QUESTION = range(5)


def run(pieces, post, cap=None, sbd=None, pre=None):
    """Decode ``pieces`` with per-piece labels (no marks before, by default)."""
    n = len(pieces)
    return decode(
        pieces,
        LABELS,
        pre=pre or [0] * n,
        post=post,
        cap=cap or [[False] * 8 for _ in range(n)],
        sbd=sbd or [False] * n,
    )


def upper_first(*flags):
    """Capitalisation rows that upper-case the first character after "▁"."""
    return [[False, flag, *([False] * 6)] for flag in flags]


def test_marks_case_and_sentence_boundaries():
    """Marks follow their piece, case is per character, sentences join with " "."""
    pieces = ["▁привет", "▁мир", "▁как", "▁дела"]
    text = run(
        pieces,
        post=[COMMA, DOT, NULL, QUESTION],
        cap=upper_first(True, False, True, False),
        sbd=[False, True, False, True],
    )
    assert text == "Привет, мир. Как дела?"


def test_marks_go_after_the_last_character_of_a_word_split_in_pieces():
    """A word of several pieces gets its mark after its last piece only."""
    pieces = ["▁эконом", "ики", "▁китая"]
    text = run(pieces, post=[NULL, NULL, DOT], cap=upper_first(False, False, True))
    assert text == "экономики Китая."


def test_acronym_label_puts_a_period_after_each_letter():
    """<ACRONYM> writes U.S.A.-style abbreviations."""
    cap = [[False, True, True, True, False, False, False, False]]
    assert run(["▁usa"], post=[ACRONYM], cap=cap) == "U.S.A."


def test_pre_label_goes_before_the_first_character():
    """Spanish inverted question marks come before the word."""
    pieces = ["▁qué", "▁tal"]
    text = run(pieces, post=[NULL, QUESTION], pre=[1, 0])
    assert text == "¿qué tal?"


def test_bare_word_start_piece_never_gets_a_mark():
    """A lone "▁" piece (no characters) gets no mark: the package decoding."""
    pieces = ["▁", "дюваль", "▁женат"]
    text = run(pieces, post=[COMMA, COMMA, DOT], cap=[[False] * 8, [True], [False]])
    assert text == "Дюваль, женат."


def test_split_ids_cuts_between_words():
    """Long inputs are folded at word starts, within the model's limit."""
    pieces = ["▁a", "b", "▁c", "▁d", "e", "f", "▁g"]
    ids = list(range(len(pieces)))
    assert split_ids(ids, pieces, 4) == [[0, 1, 2], [3, 4, 5, 6]]
    assert split_ids(ids, pieces, 10) == [ids]


def test_split_ids_hard_cuts_a_word_longer_than_the_limit():
    """Without a word start in the window the cut is at the limit."""
    pieces = ["▁a", "b", "c", "d", "e"]
    assert split_ids(list(range(5)), pieces, 2) == [[0, 1], [2, 3], [4]]


class FakeTokenizer:
    """SentencePiece stand-in: one piece per word."""

    def __init__(self):
        """Empty vocabulary; ids are assigned on encoding."""
        self.pieces = ["<s>", "</s>"]

    def EncodeAsIds(self, text):
        """Return one id per word."""
        ids = []
        for word in text.split():
            self.pieces.append("▁" + word)
            ids.append(len(self.pieces) - 1)
        return ids

    def IdToPiece(self, i):
        """Return the piece of an id."""
        return self.pieces[i]

    def bos_id(self):
        """BOS id."""
        return 0

    def eos_id(self):
        """EOS id."""
        return 1


class FakeSession:
    """ONNX session stand-in: a comma after every word, a period at the end."""

    def __init__(self):
        """Record the inputs."""
        self.inputs = []

    def run(self, outputs, feeds):
        """Return pre, post, cap and sbd for the batch."""
        ids = feeds["input_ids"]
        assert ids.dtype == np.int64
        self.inputs.append(ids[0].tolist())
        n = ids.shape[1]
        post = np.full((1, n), COMMA)
        post[0, n - 2] = DOT
        cap = np.zeros((1, n, 8), dtype=bool)
        cap[0, 1, 1] = True
        sbd = np.zeros((1, n), dtype=bool)
        sbd[0, n - 2] = True
        return [np.zeros((1, n), dtype=np.int64), post, cap, sbd]


def test_punctuator_wraps_the_ids_with_bos_and_eos():
    """The model sees [BOS, ids..., EOS]; labels of BOS/EOS are ignored."""
    session = FakeSession()
    punctuator = XlmrPunctuator(session, FakeTokenizer(), LABELS, max_length=256)
    assert punctuator("раз два три") == "Раз, два, три."
    assert session.inputs == [[0, 2, 3, 4, 1]]
    assert punctuator("   ") == ""
    assert len(session.inputs) == 1


def test_punctuator_folds_long_inputs():
    """Inputs longer than max_length are split over several model calls."""
    session = FakeSession()
    punctuator = XlmrPunctuator(session, FakeTokenizer(), LABELS, max_length=5)
    text = punctuator("a b c d e f g")
    assert [len(ids) for ids in session.inputs] == [5, 5, 3]
    assert text == "A, b, c. D, e, f. G."
