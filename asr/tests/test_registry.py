"""The model registry matches the contract: ids, labels, pinned revisions."""

import re

from asr_service.registry import (
    DEFAULT_MODEL,
    DEFAULT_MODELS,
    MODELS,
    PUNCTUATORS,
    RECOGNIZERS,
    vram_variable,
)

SHA = re.compile(r"[0-9a-f]{40}")


def test_model_ids_and_default():
    """Four models, gigaam-v3 first and default."""
    assert DEFAULT_MODELS == (
        "gigaam-v3",
        "gigaam-multilingual",
        "gigaam-multilingual-punct",
    )
    assert DEFAULT_MODEL == "gigaam-v3"


def test_labels_are_the_russian_ui_names():
    """The labels shown on the website."""
    assert {model.id: model.label for model in MODELS.values()} == {
        "gigaam-v3": "Русский — GigaAM v3",
        "gigaam-multilingual": "Прежняя модель — GigaAM Multilingual",
        "gigaam-multilingual-punct": "GigaAM Multilingual с пунктуацией",
    }


def test_every_model_points_at_existing_components():
    """Recognisers and punctuators referenced by the models exist."""
    for model in MODELS.values():
        assert model.recognizer in RECOGNIZERS
        assert model.punctuator is None or model.punctuator in PUNCTUATORS
        assert model.languages[0] == "ru"
    assert MODELS["gigaam-multilingual-punct"].recognizer == "gigaam-multilingual"


def test_every_revision_is_a_commit_sha():
    """Weights and code are pinned by commit, never by branch."""
    revisions = []
    for recognizer in RECOGNIZERS.values():
        revisions.append(recognizer.cpu.revision)
        if recognizer.gpu is not None:
            revisions.append(recognizer.gpu.revision)
            if recognizer.gpu.code_repo is not None:
                revisions.append(recognizer.gpu.code_revision)
    revisions += [punctuator.revision for punctuator in PUNCTUATORS.values()]
    assert all(SHA.fullmatch(revision) for revision in revisions)


def test_backends_follow_the_benchmark_decision():
    """GPU on torch for the GigaAM models only; the right CPU quantizations."""
    v3 = RECOGNIZERS["gigaam-v3"]
    assert v3.gpu.repo == "ai-sage/GigaAM-v3"
    assert v3.gpu.revision == "7655ad717f8122257385bb4b2f373db3697e8680"
    assert v3.gpu.code_repo == "ai-sage/GigaAM-Multilingual"
    assert v3.gpu.code_revision == "3905cd51c3ed4e88c8edf33f3302969ba480a327"
    assert v3.gpu.vram_gb == 1.5
    assert v3.cpu.quantization == "int8"
    multilingual = RECOGNIZERS["gigaam-multilingual"]
    assert multilingual.gpu.vram_gb == 3.0
    assert multilingual.gpu.code_repo is None
    assert multilingual.cpu.quantization == "int8"
    assert PUNCTUATORS["xlmr-pcs"].revision == (
        "d1769a597ce8dfaa070d436bc67d4ee761f58884"
    )


def test_vram_variable_names():
    """ASR_VRAM_GB_<ID upper, - -> _>."""
    assert vram_variable("gigaam-v3") == "ASR_VRAM_GB_GIGAAM_V3"
    assert vram_variable("gigaam-multilingual") == "ASR_VRAM_GB_GIGAAM_MULTILINGUAL"
