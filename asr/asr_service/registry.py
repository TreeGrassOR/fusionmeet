"""The recognition models this service can serve, with their pinned weights.

A model (``ModelSpec``) is what a client picks with ``?model=<id>``. It is run by a
recogniser (``Recognizer``): GigaAM through torch on the GPU, or an ONNX export through
onnx-asr on the CPU. ``gigaam-multilingual-punct`` shares the recogniser of
``gigaam-multilingual`` and adds a punctuation and true-casing model on the CPU.
Labels and descriptions are shown on the website, in Russian.
"""

from __future__ import annotations

from dataclasses import dataclass

# ai-sage/GigaAM-Multilingual, branch "large_ctc" (585M parameters). Its
# modeling_gigaam.GigaAMModel class also loads the GigaAM-v3 weights (see TorchBackend).
GIGAAM_MULTILINGUAL_REPO = "ai-sage/GigaAM-Multilingual"
GIGAAM_MULTILINGUAL_REVISION = "3905cd51c3ed4e88c8edf33f3302969ba480a327"


@dataclass(frozen=True)
class TorchBackend:
    """GigaAM weights run by transformers and torch on CUDA, batched."""

    repo: str
    revision: str
    variant: str
    # GPU memory the model needs with its warm-up batch (GiB); ASR_VRAM_GB_<ID>
    # overrides it.
    vram_gb: float
    # Take the GigaAMModel class from this repository instead of the weights' own
    # remote code: the GigaAM-v3 code does not load on transformers 5.20.1.
    code_repo: str | None = None
    code_revision: str | None = None


@dataclass(frozen=True)
class OnnxBackend:
    """An onnx-asr model run on the CPU by onnxruntime, one clip at a time."""

    model_type: str
    repo: str
    revision: str
    # The files downloaded from ``repo``: onnx-asr picks its model files by pattern,
    # so only one quantization may be present in the directory.
    files: tuple[str, ...]
    quantization: str | None
    variant: str


@dataclass(frozen=True)
class Recognizer:
    """Speech-to-text engine of one or more models."""

    key: str
    cpu: OnnxBackend
    gpu: TorchBackend | None = None


@dataclass(frozen=True)
class Punctuator:
    """Punctuation and true-casing model applied to a recogniser's text (CPU)."""

    key: str
    repo: str
    revision: str
    files: tuple[str, ...]


@dataclass(frozen=True)
class ModelSpec:
    """A selectable model, as listed by ``GET /v1/models``."""

    id: str
    label: str
    description: str
    languages: tuple[str, ...]
    punctuation: bool
    recognizer: str
    punctuator: str | None = None


RECOGNIZERS: dict[str, Recognizer] = {
    recognizer.key: recognizer
    for recognizer in (
        Recognizer(
            key="gigaam-v3",
            gpu=TorchBackend(
                repo="ai-sage/GigaAM-v3",
                # branch "e2e_rnnt"
                revision="7655ad717f8122257385bb4b2f373db3697e8680",
                variant="v3_e2e_rnnt",
                vram_gb=1.5,
                code_repo=GIGAAM_MULTILINGUAL_REPO,
                code_revision=GIGAAM_MULTILINGUAL_REVISION,
            ),
            cpu=OnnxBackend(
                model_type="gigaam-v3-e2e-rnnt",
                repo="istupakov/gigaam-v3-onnx",
                revision="322c3b29492673eb7d0b434bfa9dfb8653e34d02",
                files=(
                    "config.json",
                    "v3_e2e_rnnt_encoder.int8.onnx",
                    "v3_e2e_rnnt_decoder.int8.onnx",
                    "v3_e2e_rnnt_joint.int8.onnx",
                    "v3_e2e_rnnt_vocab.txt",
                ),
                quantization="int8",
                variant="v3_e2e_rnnt-onnx-int8",
            ),
        ),
        Recognizer(
            key="gigaam-multilingual",
            gpu=TorchBackend(
                repo=GIGAAM_MULTILINGUAL_REPO,
                revision=GIGAAM_MULTILINGUAL_REVISION,
                variant="large_ctc",
                vram_gb=3.0,
            ),
            cpu=OnnxBackend(
                model_type="gigaam-multilingual-large-ctc",
                repo="istupakov/gigaam-multilingual-large-ctc-onnx",
                revision="07665ab5e54371dd1ac7b8b10f06478003723573",
                files=(
                    "config.json",
                    "multilingual_large_ctc.int8.onnx",
                    "multilingual_vocab.txt",
                ),
                quantization="int8",
                variant="large_ctc-onnx-int8",
            ),
        ),
    )
}

PUNCTUATORS: dict[str, Punctuator] = {
    "xlmr-pcs": Punctuator(
        key="xlmr-pcs",
        repo="1-800-BAD-CODE/xlm-roberta_punctuation_fullstop_truecase",
        revision="d1769a597ce8dfaa070d436bc67d4ee761f58884",
        files=("model.onnx", "sp.model", "config.yaml"),
    ),
}

_GIGAAM_MULTILINGUAL_LANGUAGES = ("ru", "en", "kk", "ky", "uz")

MODELS: dict[str, ModelSpec] = {
    model.id: model
    for model in (
        ModelSpec(
            id="gigaam-v3",
            label="Русский — GigaAM v3",
            description=(
                "Лучшее качество для русской речи, с пунктуацией и заглавными "
                "буквами. Английскую речь не распознаёт."
            ),
            languages=("ru",),
            punctuation=True,
            recognizer="gigaam-v3",
        ),
        ModelSpec(
            id="gigaam-multilingual",
            label="Прежняя модель — GigaAM Multilingual",
            description=(
                "Модель, которая работала до обновления: строчные буквы без знаков "
                "препинания."
            ),
            languages=_GIGAAM_MULTILINGUAL_LANGUAGES,
            punctuation=False,
            recognizer="gigaam-multilingual",
        ),
        ModelSpec(
            id="gigaam-multilingual-punct",
            label="GigaAM Multilingual с пунктуацией",
            description=(
                "Прежняя модель; знаки препинания и заглавные буквы расставляет "
                "дополнительная модель."
            ),
            languages=_GIGAAM_MULTILINGUAL_LANGUAGES,
            punctuation=True,
            recognizer="gigaam-multilingual",
            punctuator="xlmr-pcs",
        ),
    )
}

DEFAULT_MODELS = tuple(MODELS)
DEFAULT_MODEL = "gigaam-v3"


def vram_variable(recognizer_key: str) -> str:
    """Name of the variable that overrides a recogniser's VRAM need."""
    return "ASR_VRAM_GB_" + recognizer_key.upper().replace("-", "_")
