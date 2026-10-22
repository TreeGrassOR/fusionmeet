"""Tests of the model manager: placement, load order, CPU fallback, statuses."""

import dataclasses
import logging

from asr_service.engine import GpuLoadError
from asr_service.registry import MODELS, RECOGNIZERS, ModelSpec, Recognizer

from .conftest import (
    AUTH,
    FakeBackends,
    fake_gpu,
    make_settings,
    oom,
    running_app,
    wait_loaded,
)


async def test_gpu_models_load_on_the_gpu_and_the_rest_on_cpu():
    """With CUDA, gigaam-v3 goes to the GPU; the allocator is limited to the budget."""
    backends = FakeBackends(gpu=fake_gpu(free_gb=10, total_gb=24))
    async with running_app(make_settings(device="cuda"), backends) as (app, client):
        await wait_loaded(app)
        models = (await client.get("/v1/models", headers=AUTH)).json()["models"]
        health = (await client.get("/health")).json()
    assert backends.loaded == [
        ("gigaam-v3", "cuda:0"),
        ("gigaam-multilingual", "cpu"),
        ("xlmr-pcs", "cpu"),
    ]
    assert {m["id"]: m["device"] for m in models} == {
        "gigaam-v3": "cuda",
        "gigaam-multilingual": "cpu",
        "gigaam-multilingual-punct": "cpu",
    }
    [(gpu, budget)] = backends.limits
    assert budget == 4 * 1024**3
    assert gpu.index == 0
    assert health["vram_limit_gb"] == 4.0
    assert health["vram_free_gb_at_start"] == 10.0


async def test_gpu_out_of_memory_while_loading_falls_back_to_cpu(caplog):
    """A CUDA OOM while loading a GPU model loads its CPU backend instead."""
    backends = FakeBackends(
        gpu=fake_gpu(), failures={"gigaam-v3@gpu": oom("gigaam-v3")}
    )
    with caplog.at_level(logging.WARNING, logger="asr_service"):
        async with running_app(make_settings(device="cuda"), backends) as (
            app,
            client,
        ):
            await wait_loaded(app)
            models = (await client.get("/v1/models", headers=AUTH)).json()["models"]
    assert backends.loaded[:2] == [("gigaam-v3", "cuda:0"), ("gigaam-v3", "cpu")]
    assert models[0]["device"] == "cpu"
    assert models[0]["status"] == "ready"
    assert any(
        "gigaam-v3 runs on the CPU instead" in record.getMessage()
        for record in caplog.records
    )


async def test_other_gpu_load_errors_fall_back_to_cpu(caplog):
    """Another failure on the GPU (CUDA error, download) also loads the CPU backend."""
    error = GpuLoadError("Loading ai-sage/GigaAM-v3 on cuda:0 failed: CUDA error")
    backends = FakeBackends(gpu=fake_gpu(), failures={"gigaam-v3@gpu": error})
    with caplog.at_level(logging.WARNING, logger="asr_service"):
        async with running_app(make_settings(device="cuda"), backends) as (
            app,
            client,
        ):
            await wait_loaded(app)
            models = (await client.get("/v1/models", headers=AUTH)).json()["models"]
            health = await client.get("/health")
    assert backends.loaded[:2] == [("gigaam-v3", "cuda:0"), ("gigaam-v3", "cpu")]
    assert models[0]["status"] == "ready"
    assert models[0]["device"] == "cpu"
    assert health.status_code == 200
    assert any(
        "CUDA error; gigaam-v3 runs on the CPU instead" in record.getMessage()
        for record in caplog.records
    )


async def test_gpu_and_cpu_failures_fail_the_model():
    """When the CPU backend fails too, the model is failed with the CPU error."""
    backends = FakeBackends(
        gpu=fake_gpu(),
        failures={
            "gigaam-v3@gpu": GpuLoadError("Loading on cuda:0 failed: OSError"),
            "gigaam-v3": OSError("cannot reach the Hub"),
        },
    )
    async with running_app(make_settings(device="cuda"), backends) as (app, client):
        await wait_loaded(app)
        models = (await client.get("/v1/models", headers=AUTH)).json()["models"]
    assert backends.loaded[:2] == [("gigaam-v3", "cuda:0"), ("gigaam-v3", "cpu")]
    assert models[0]["status"] == "failed"
    assert models[0]["error"] == "OSError: cannot reach the Hub"
    assert models[0]["device"] is None


async def test_no_cuda_loads_everything_on_cpu(caplog):
    """ASR_DEVICE=cuda without a GPU no longer exits: every model runs on the CPU."""
    backends = FakeBackends(no_gpu_reason="CUDA is not available (test)")
    with caplog.at_level(logging.WARNING, logger="asr_service"):
        async with running_app(make_settings(device="cuda"), backends) as (
            app,
            client,
        ):
            await wait_loaded(app)
            health = await client.get("/health")
    assert health.status_code == 200
    assert {device for _key, device in backends.loaded} == {"cpu"}
    assert backends.limits == []
    assert any("CUDA is not available (test)" in r.getMessage() for r in caplog.records)


async def test_a_failing_gpu_probe_is_treated_as_no_cuda():
    """An exception while reading the GPU memory sends everything to the CPU."""

    class Broken(FakeBackends):
        def probe_gpu(self, settings):
            raise RuntimeError("CUDA driver error")

    backends = Broken()
    async with running_app(make_settings(device="cuda"), backends) as (app, client):
        await wait_loaded(app)
        assert (await client.get("/health")).status_code == 200
    assert {device for _key, device in backends.loaded} == {"cpu"}


async def test_cpu_device_does_not_probe_the_gpu():
    """ASR_DEVICE=cpu never imports torch or touches CUDA."""
    backends = FakeBackends(gpu=fake_gpu())
    async with running_app(make_settings(device="cpu"), backends) as (app, _client):
        await wait_loaded(app)
    assert backends.probes == 0
    assert {device for _key, device in backends.loaded} == {"cpu"}


async def test_cuda_without_gpu_capable_models_does_not_probe_the_gpu(
    caplog, monkeypatch
):
    """Without a model that has a GPU backend, no CUDA context is created."""
    # Every model of the registry can use the GPU: register a CPU-only one
    cpu = dataclasses.replace(RECOGNIZERS["gigaam-v3"].cpu, variant="cpu-only")
    cpu_only = Recognizer(key="cpu-only", cpu=cpu)
    monkeypatch.setitem(RECOGNIZERS, "cpu-only", cpu_only)
    monkeypatch.setitem(
        MODELS,
        "cpu-only",
        ModelSpec(
            id="cpu-only",
            label="CPU only",
            description="",
            languages=("ru",),
            punctuation=False,
            recognizer="cpu-only",
        ),
    )
    backends = FakeBackends(gpu=fake_gpu())
    settings = make_settings(
        device="cuda", models=("cpu-only",), default_model="cpu-only"
    )
    with caplog.at_level(logging.INFO, logger="asr_service"):
        async with running_app(settings, backends) as (app, client):
            await wait_loaded(app)
            assert (await client.get("/health")).status_code == 200
    assert backends.probes == 0
    assert backends.limits == []
    assert backends.loaded == [("cpu-only", "cpu")]
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_default_model_loads_first():
    """The default model is loaded (and placed) before the others."""
    backends = FakeBackends(gpu=fake_gpu(free_gb=3.5))
    settings = make_settings(device="cuda", default_model="gigaam-multilingual-punct")
    async with running_app(settings, backends) as (app, _client):
        await wait_loaded(app)
    assert backends.loaded == [
        ("gigaam-multilingual", "cuda:0"),
        ("xlmr-pcs", "cpu"),
        ("gigaam-v3", "cpu"),
    ]


async def test_punctuated_model_alone_loads_its_recogniser():
    """gigaam-multilingual-punct without gigaam-multilingual still has its engine."""
    settings = make_settings(
        models=("gigaam-multilingual-punct",),
        default_model="gigaam-multilingual-punct",
    )
    backends = FakeBackends()
    async with running_app(settings, backends) as (app, client):
        await wait_loaded(app)
        body = (await client.get("/v1/models", headers=AUTH)).json()
    assert backends.loaded == [("gigaam-multilingual", "cpu"), ("xlmr-pcs", "cpu")]
    assert [model["id"] for model in body["models"]] == ["gigaam-multilingual-punct"]


async def test_only_enabled_models_are_loaded():
    """ASR_MODELS=gigaam-v3 loads nothing else."""
    backends = FakeBackends()
    async with running_app(make_settings(models=("gigaam-v3",)), backends) as (
        app,
        _client,
    ):
        await wait_loaded(app)
    assert backends.loaded == [("gigaam-v3", "cpu")]


async def test_a_failing_gpu_limit_falls_back_to_cpu_placement():
    """If the allocator limit cannot be set, nothing is loaded on the GPU."""

    class Broken(FakeBackends):
        def limit_gpu_memory(self, gpu, budget_bytes):
            raise ValueError("bad fraction")

    backends = Broken(gpu=fake_gpu())
    async with running_app(make_settings(device="cuda"), backends) as (app, client):
        await wait_loaded(app)
        assert (await client.get("/health")).status_code == 200
    assert {device for _key, device in backends.loaded} == {"cpu"}
