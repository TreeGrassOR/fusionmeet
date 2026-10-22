"""Tests of the HTTP API with a fake engine."""

import asyncio
import threading

import numpy as np
import pytest

from asr_service.audio import SAMPLE_RATE

from .conftest import (
    AUTH,
    PCM,
    WAV,
    FakeBackends,
    FakeEngine,
    fake_gpu,
    make_settings,
    pcm16,
    running_app,
    tone,
    wait_loaded,
    wait_ready,
    wait_until,
    wav_custom,
    wav_float32,
    wav_pcm16,
)


def pending(app, model_id: str = "gigaam-v3") -> int:
    """Clips waiting in the queue of a model."""
    manager = app.state.asr.manager
    return manager.pending(manager.models[model_id])


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong-key"},
        {"Authorization": "Basic dGVzdC1zZWNyZXQta2V5"},
        {"Authorization": "test-secret-key"},
    ],
)
async def test_missing_or_wrong_api_key_is_401(client, engine, headers):
    """Requests without the right Bearer key are refused before any decoding."""
    response = await client.post(
        "/v1/transcribe",
        content=wav_pcm16(tone(1)),
        headers={"Content-Type": "audio/wav", **headers},
    )
    assert response.status_code == 401
    assert response.json() == {"detail": "Missing or invalid API key"}
    assert response.headers["www-authenticate"] == "Bearer"
    assert engine.calls == []


async def test_wav_pcm16_transcription(client, engine):
    """A 16-bit WAV is decoded and its text returned with the model description."""
    samples = tone(1.5)
    response = await client.post(
        "/v1/transcribe", content=wav_pcm16(samples), headers=WAV
    )
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "clip 24000"
    assert body["duration"] == 1.5
    assert body["model_id"] == "gigaam-v3"
    assert body["model_label"] == "Русский — GigaAM v3"
    assert body["model"] == "ai-sage/GigaAM-v3"
    assert body["revision"] == "rev-v3"
    assert body["variant"] == "v3"
    assert isinstance(body["processing_ms"], int)
    np.testing.assert_allclose(engine.received[0], samples, atol=1e-4)


async def test_wav_float32_transcription(client, engine):
    """A 32-bit float WAV reaches the model unchanged."""
    samples = tone(0.5)
    response = await client.post(
        "/v1/transcribe",
        content=wav_float32(samples),
        headers={**AUTH, "Content-Type": "audio/x-wav"},
    )
    assert response.status_code == 200
    assert response.json()["text"] == "clip 8000"
    np.testing.assert_array_equal(engine.received[0], samples)


@pytest.mark.parametrize(
    "content_type", ["audio/pcm", "application/octet-stream", "audio/l16"]
)
async def test_raw_pcm_transcription(client, engine, content_type):
    """Raw s16le PCM is accepted."""
    samples = tone(0.75)
    response = await client.post(
        "/v1/transcribe",
        content=pcm16(samples),
        headers={**AUTH, "Content-Type": content_type},
    )
    assert response.status_code == 200
    assert response.json()["text"] == "clip 12000"
    np.testing.assert_allclose(engine.received[0], samples, atol=1e-4)


@pytest.mark.parametrize(
    ("body", "content_type", "detail"),
    [
        (
            wav_pcm16(tone(1, rate=8000), rate=8000),
            "audio/wav",
            "Unsupported sample rate 8000 Hz",
        ),
        (wav_pcm16(tone(1), channels=2), "audio/wav", "Unsupported channel count 2"),
        (wav_custom(b"\x00" * 3000, bits=24), "audio/wav", "24-bit PCM"),
        (pcm16(tone(1)), "audio/pcm; rate=44100", "Unsupported sample rate 44100 Hz"),
        (b"OggS\x00\x02fake", "audio/ogg", "Unsupported Content-Type"),
    ],
)
async def test_wrong_format_is_422(client, engine, body, content_type, detail):
    """Wrong rate, channels, codec or media type is a 422 with a clear message."""
    response = await client.post(
        "/v1/transcribe", content=body, headers={**AUTH, "Content-Type": content_type}
    )
    assert response.status_code == 422
    assert detail in response.json()["detail"]
    assert engine.calls == []


@pytest.mark.parametrize(
    ("body", "content_type"),
    [
        (b"\x89PNG\r\n\x1a\n not audio at all", "audio/wav"),
        (b"RIFF\x10\x00\x00\x00WAVEjunk", "audio/wav"),
        (b"\x00\x01\x02", "audio/pcm"),
        (b"", "audio/pcm"),
    ],
)
async def test_garbage_is_400(client, engine, body, content_type):
    """Undecodable bodies are a 400."""
    response = await client.post(
        "/v1/transcribe", content=body, headers={**AUTH, "Content-Type": content_type}
    )
    assert response.status_code == 400
    assert response.json()["detail"]
    assert engine.calls == []


async def test_too_long_audio_is_413(client, engine):
    """Audio longer than ASR_MAX_AUDIO_SECONDS is refused."""
    response = await client.post(
        "/v1/transcribe", content=pcm16(tone(30.5)), headers=PCM
    )
    assert response.status_code == 413
    assert "the limit is 30 s" in response.json()["detail"]
    assert engine.calls == []


async def test_exactly_max_duration_is_accepted(client):
    """The limit itself is allowed."""
    response = await client.post("/v1/transcribe", content=pcm16(tone(30)), headers=PCM)
    assert response.status_code == 200


async def test_oversized_body_is_413_before_decoding(client, engine):
    """A body larger than any valid request is refused from its size alone."""
    body = b"\x00" * (30 * SAMPLE_RATE * 4 + 64 * 1024 + 2)
    response = await client.post("/v1/transcribe", content=body, headers=PCM)
    assert response.status_code == 413
    assert "larger than" in response.json()["detail"]
    assert engine.calls == []


@pytest.mark.parametrize("samples", [0, 1, 319, 1599])
async def test_tiny_audio_returns_empty_text_without_the_model(client, engine, samples):
    """Clips shorter than 0.1 s are answered with "" and never reach the model."""
    body = wav_pcm16(np.zeros(samples, dtype=np.float32))
    response = await client.post("/v1/transcribe", content=body, headers=WAV)
    assert response.status_code == 200
    assert response.json()["text"] == ""
    assert response.json()["duration"] == round(samples / SAMPLE_RATE, 3)
    assert engine.calls == []


def on_gpu(engine: FakeEngine) -> FakeBackends:
    """Fake backends with a GPU that holds the default model (``engine``)."""
    return FakeBackends({"gigaam-v3": engine}, gpu=fake_gpu())


async def test_concurrent_requests_are_batched():
    """Concurrent requests to a GPU model share model calls; each gets its text."""
    engine = FakeEngine(delay=0.05)
    settings = make_settings(device="cuda", batch_wait_ms=50, max_batch_size=8)
    lengths = [SAMPLE_RATE + 1000 * index for index in range(6)]
    async with running_app(settings, on_gpu(engine)) as (_app, client):
        await wait_ready(client)
        responses = await asyncio.gather(
            *(
                client.post(
                    "/v1/transcribe", content=pcm16(tone(n / SAMPLE_RATE)), headers=PCM
                )
                for n in lengths
            )
        )
    assert [r.status_code for r in responses] == [200] * len(lengths)
    assert [r.json()["text"] for r in responses] == [f"clip {n}" for n in lengths]
    assert len(engine.calls) < len(lengths)
    assert max(len(call) for call in engine.calls) > 1
    assert sorted(n for call in engine.calls for n in call) == lengths


async def test_batches_respect_max_batch_size():
    """No model call receives more than ASR_MAX_BATCH_SIZE clips."""
    engine = FakeEngine(delay=0.02)
    settings = make_settings(device="cuda", batch_wait_ms=30, max_batch_size=3)
    async with running_app(settings, on_gpu(engine)) as (_app, client):
        await wait_ready(client)
        responses = await asyncio.gather(
            *(
                client.post("/v1/transcribe", content=pcm16(tone(0.5)), headers=PCM)
                for _ in range(10)
            )
        )
    assert all(r.status_code == 200 for r in responses)
    assert sum(len(call) for call in engine.calls) == 10
    assert max(len(call) for call in engine.calls) <= 3


async def test_cpu_models_take_one_clip_per_call():
    """On the CPU concurrent requests are queued but never batched."""
    engine = FakeEngine(delay=0.02)
    settings = make_settings(batch_wait_ms=50, max_batch_size=8)
    async with running_app(settings, FakeBackends({"gigaam-v3": engine})) as (
        _app,
        client,
    ):
        await wait_ready(client)
        responses = await asyncio.gather(
            *(
                client.post("/v1/transcribe", content=pcm16(tone(0.5)), headers=PCM)
                for _ in range(5)
            )
        )
    assert [r.status_code for r in responses] == [200] * 5
    assert engine.calls == [[8000]] * 5


async def test_queue_full_is_503():
    """When ASR_MAX_QUEUE requests are already waiting, new ones get a 503."""
    gate = threading.Event()
    engine = FakeEngine(gate=gate)
    settings = make_settings(max_queue=1, max_batch_size=1, batch_wait_ms=0)
    async with running_app(settings, FakeBackends({"gigaam-v3": engine})) as (
        app,
        client,
    ):
        await wait_ready(client)
        body = pcm16(tone(0.5))
        first = asyncio.create_task(
            client.post("/v1/transcribe", content=body, headers=PCM)
        )
        await wait_until(engine.entered.is_set)  # the first clip is in the model
        second = asyncio.create_task(
            client.post("/v1/transcribe", content=body, headers=PCM)
        )
        await wait_until(lambda: pending(app) == 1)

        rejected = await client.post("/v1/transcribe", content=body, headers=PCM)
        assert rejected.status_code == 503
        assert "Too many pending requests" in rejected.json()["detail"]
        assert rejected.headers["retry-after"] == "1"

        gate.set()
        assert (await first).status_code == 200
        assert (await second).status_code == 200


async def test_clip_of_a_disconnected_client_is_not_transcribed():
    """A queued clip whose client went away (timeout, retry) never reaches the model.

    The ASGI app is called directly: unlike httpx's ASGITransport, a real server
    reports the disconnect through ``receive()`` and does not cancel the handler.
    """
    gate = threading.Event()
    engine = FakeEngine(gate=gate)
    settings = make_settings(max_batch_size=1, batch_wait_ms=0)
    async with running_app(settings, FakeBackends({"gigaam-v3": engine})) as (
        app,
        client,
    ):
        await wait_ready(client)
        first = asyncio.create_task(
            client.post("/v1/transcribe", content=pcm16(tone(0.5)), headers=PCM)
        )
        await wait_until(engine.entered.is_set)  # the model is busy

        messages = [
            {"type": "http.request", "body": pcm16(tone(1)), "more_body": False}
        ]
        disconnected = asyncio.Event()
        sent = []

        async def receive():
            if messages:
                return messages.pop(0)
            await disconnected.wait()
            return {"type": "http.disconnect"}

        async def send(message):
            sent.append(message)

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/v1/transcribe",
            "raw_path": b"/v1/transcribe",
            "query_string": b"",
            "root_path": "",
            "headers": [
                (b"host", b"asr"),
                (b"authorization", AUTH["Authorization"].encode()),
                (b"content-type", b"audio/pcm"),
            ],
            "client": ("127.0.0.1", 50000),
            "server": ("asr", 8000),
        }
        abandoned = asyncio.create_task(app(scope, receive, send))
        await wait_until(lambda: pending(app) == 1)
        disconnected.set()
        await asyncio.wait_for(abandoned, 5)

        gate.set()
        assert (await first).status_code == 200
        last = await client.post(
            "/v1/transcribe", content=pcm16(tone(0.25)), headers=PCM
        )
        assert last.status_code == 200
    assert sent[0]["status"] == 499
    # the 1 s clip (16000 samples) was skipped
    assert engine.calls == [[8000], [4000]]


async def test_model_failure_is_500():
    """An exception in the model fails the request with a 500, not the service."""
    engine = FakeEngine(error=RuntimeError("boom"))
    async with running_app(make_settings(), FakeBackends({"gigaam-v3": engine})) as (
        _app,
        client,
    ):
        await wait_ready(client)
        response = await client.post(
            "/v1/transcribe", content=pcm16(tone(1)), headers=PCM
        )
        assert response.status_code == 500
        assert response.json() == {"detail": "Transcription failed"}
        health = await client.get("/health")
        assert health.status_code == 200


async def test_health_goes_from_loading_to_ok():
    """/health is 503 "loading" until the default model is loaded, then 200."""
    gate = threading.Event()
    backends = FakeBackends(gates={"gigaam-v3": gate})
    settings = make_settings(max_batch_size=5)
    async with running_app(settings, backends) as (app, client):
        loading = await client.get("/health")
        assert loading.status_code == 503
        assert loading.json()["status"] == "loading"
        assert {m["status"] for m in loading.json()["models"]} == {"loading"}

        early = await client.post("/v1/transcribe", content=pcm16(tone(1)), headers=PCM)
        assert early.status_code == 503
        assert early.json() == {"detail": "Model gigaam-v3 is still loading"}
        assert early.headers["retry-after"] == "5"

        gate.set()
        await wait_ready(client)
        await wait_loaded(app)
        ready = await client.get("/health")
    assert ready.json() == {
        "status": "ok",
        "default": "gigaam-v3",
        "models": [
            {
                "id": "gigaam-v3",
                "status": "ready",
                "device": "cpu",
                "model": "repo/gigaam-v3",
                "revision": "rev-fake",
                "variant": "gigaam-v3-cpu",
                "fp16_encoder": False,
            },
            {
                "id": "gigaam-multilingual",
                "status": "ready",
                "device": "cpu",
                "model": "repo/gigaam-multilingual",
                "revision": "rev-fake",
                "variant": "gigaam-multilingual-cpu",
                "fp16_encoder": False,
            },
            {
                "id": "gigaam-multilingual-punct",
                "status": "ready",
                "device": "cpu",
                "model": "repo/gigaam-multilingual",
                "revision": "rev-fake",
                "variant": "gigaam-multilingual-cpu",
                "fp16_encoder": False,
            },
        ],
        "vram_limit_gb": None,
        "vram_free_gb_at_start": None,
        "cpu_threads": 2,
        "max_batch_size": 5,
    }


async def test_health_needs_no_auth_but_hides_nothing_secret(client):
    """/health is public and never echoes the API key."""
    response = await client.get("/health")
    assert response.status_code == 200
    assert "test-secret-key" not in response.text
