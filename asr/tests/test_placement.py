"""Tests of the GPU/CPU placement plan."""

import logging

import pytest

from asr_service.placement import GIB, plan_placement
from asr_service.registry import RECOGNIZERS

from .conftest import fake_gpu, make_settings

ALL = [RECOGNIZERS[key] for key in ("gigaam-v3", "parakeet-v3", "gigaam-multilingual")]


def devices(placement):
    """Device per recogniser key."""
    return placement.devices


def warnings(caplog):
    """WARNING messages of the service."""
    return [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]


def test_default_budget_fits_gigaam_v3_only(caplog):
    """4 GiB: gigaam-v3 (1.5) on the GPU, gigaam-multilingual (3.0) on the CPU."""
    with caplog.at_level(logging.INFO, logger="asr_service"):
        placement = plan_placement(make_settings(device="cuda"), ALL, fake_gpu(10))
    assert devices(placement) == {
        "gigaam-v3": "cuda:0",
        "parakeet-v3": "cpu",
        "gigaam-multilingual": "cpu",
    }
    assert placement.budget_bytes == 4 * GIB
    assert placement.memory_fraction == pytest.approx(4 / 24)
    assert placement.uses_gpu
    [warning] = warnings(caplog)
    assert "Not enough VRAM for gigaam-multilingual" in warning
    assert "needs 3.00 GiB but only 2.50 GiB of the 4.00 GiB budget is left" in warning


def test_large_budget_fits_both_gigaam_models():
    """With 6 GiB both GPU-capable models go to the GPU; Parakeet stays on CPU."""
    placement = plan_placement(
        make_settings(device="cuda", max_vram_gb=6), ALL, fake_gpu(10)
    )
    assert devices(placement) == {
        "gigaam-v3": "cuda:0",
        "parakeet-v3": "cpu",
        "gigaam-multilingual": "cuda:0",
    }


def test_free_memory_limits_the_budget():
    """The budget is min(ASR_MAX_VRAM_GB, free memory after the CUDA context)."""
    placement = plan_placement(
        make_settings(device="cuda", max_vram_gb=8), ALL, fake_gpu(free_gb=2.0)
    )
    assert placement.budget_bytes == 2 * GIB
    assert devices(placement)["gigaam-v3"] == "cuda:0"
    assert devices(placement)["gigaam-multilingual"] == "cpu"


def test_priority_order_decides_who_gets_the_gpu():
    """The first recogniser in the list (the default model's) is placed first."""
    order = [RECOGNIZERS["gigaam-multilingual"], RECOGNIZERS["gigaam-v3"]]
    placement = plan_placement(make_settings(device="cuda"), order, fake_gpu(3.5))
    assert devices(placement) == {"gigaam-multilingual": "cuda:0", "gigaam-v3": "cpu"}


def test_nothing_fits_runs_everything_on_cpu(caplog):
    """A tiny budget sends every model to the CPU with a warning each."""
    with caplog.at_level(logging.WARNING, logger="asr_service"):
        placement = plan_placement(make_settings(device="cuda"), ALL, fake_gpu(1.0))
    assert set(devices(placement).values()) == {"cpu"}
    assert not placement.uses_gpu
    assert len(warnings(caplog)) == 2


def test_vram_override_changes_the_need():
    """ASR_VRAM_GB_<ID> replaces the registry estimate."""
    settings = make_settings(device="cuda", vram_gb={"gigaam-multilingual": 2.5})
    placement = plan_placement(settings, ALL, fake_gpu(10))
    assert devices(placement)["gigaam-multilingual"] == "cuda:0"


def test_no_cuda_runs_everything_on_cpu_with_a_warning(caplog):
    """ASR_DEVICE=cuda without a usable GPU is a warning, not an exit."""
    with caplog.at_level(logging.WARNING, logger="asr_service"):
        placement = plan_placement(
            make_settings(device="cuda"), ALL, None, "CUDA is not available here"
        )
    assert set(devices(placement).values()) == {"cpu"}
    assert placement.memory_fraction is None
    [warning] = warnings(caplog)
    assert "CUDA is not available here" in warning
    assert "gigaam-v3, gigaam-multilingual run(s) on the CPU" in warning


def test_cpu_device_never_touches_the_gpu(caplog):
    """ASR_DEVICE=cpu places everything on the CPU, without warnings."""
    with caplog.at_level(logging.INFO, logger="asr_service"):
        placement = plan_placement(make_settings(device="cpu"), ALL, fake_gpu(10))
    assert set(devices(placement).values()) == {"cpu"}
    assert placement.gpu is None
    assert warnings(caplog) == []


def test_gpu_index_is_kept():
    """cuda:<n> places models on GPU n."""
    placement = plan_placement(
        make_settings(device="cuda:1"), ALL[:1], fake_gpu(10, index=1)
    )
    assert devices(placement) == {"gigaam-v3": "cuda:1"}
