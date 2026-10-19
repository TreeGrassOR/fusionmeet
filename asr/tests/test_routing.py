"""Model selection over HTTP: ?model=, /v1/models, statuses and text clean-up."""

import asyncio
import threading

import pytest

from .conftest import (
    AUTH,
    PCM,
    FakeBackends,
    FakeEngine,
    make_settings,
    pcm16,
    running_app,
    tone,
    wait_loaded,
    wait_ready,
    wait_until,
)


async def transcribe(client, model=None, seconds=1.0):
    """POST one tone clip, optionally with ?model=."""
    params = {} if model is None else {"model": model}
    return await client.post(
        "/v1/transcribe", content=pcm16(tone(seconds)), headers=PCM, params=params
    )


@pytest.mark.parametrize(
    ("model", "text", "label"),
    [
        (None, "clip 16000", "Русский — GigaAM v3"),
        ("", "clip 16000", "Русский — GigaAM v3"),
        ("gigaam-v3", "clip 16000", "Русский — GigaAM v3"),
        ("parakeet-v3", "parakeet-v3 16000", "Русский и английский — Parakeet v3"),
        (
            "gigaam-multilingual",
            "gigaam-multilingual 16000",
            "Прежняя модель — GigaAM Multilingual",
        ),
        ("PARAKEET-V3", "parakeet-v3 16000", "Русский и английский — Parakeet v3"),
    ],
)
async def test_each_request_goes_to_its_model(client, model, text, label):
    """?model= picks the recogniser; no parameter means the default model."""
    response = await transcribe(client, model)
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == text
    assert body["model_label"] == label
    assert body["model_id"] == (model or "gigaam-v3").lower()


async def test_punctuated_model_shares_the_recogniser(client, backends):
    """gigaam-multilingual-punct = the gigaam-multilingual engine + the punctuator."""
    response = await transcribe(client, "gigaam-multilingual-punct")
    assert response.status_code == 200
    body = response.json()
    assert body["text"] == "P(gigaam-multilingual 16000)"
    assert body["model_id"] == "gigaam-multilingual-punct"
    assert body["model_label"] == "GigaAM Multilingual с пунктуацией"
    assert body["model"] == "repo/gigaam-multilingual"
    assert backends.punctuated == ["gigaam-multilingual 16000"]
    # one recogniser for both models: loaded once
    assert [key for key, _ in backends.loaded].count("gigaam-multilingual") == 1


async def test_unknown_model_is_400(client, engine):
    """An id that is not in the registry is refused before the body is read."""
    response = await transcribe(client, "whisper-large")
    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Unknown model 'whisper-large'; enabled models: gigaam-v3, parakeet-v3, "
        "gigaam-multilingual, gigaam-multilingual-punct"
    )
    assert engine.calls == []


async def test_disabled_model_is_400():
    """A known model that is not in ASR_MODELS is refused."""
    settings = make_settings(models=("gigaam-v3",))
    async with running_app(settings) as (_app, client):
        await wait_ready(client)
        response = await transcribe(client, "parakeet-v3")
    assert response.status_code == 400
    assert response.json()["detail"] == (
        "Model 'parakeet-v3' is not enabled on this server; enabled models: gigaam-v3"
    )


async def test_auth_is_checked_before_the_model():
    """A wrong key is a 401 even for an unknown model."""
    async with running_app(make_settings()) as (_app, client):
        response = await client.post(
            "/v1/transcribe",
            params={"model": "nope"},
            content=pcm16(tone(1)),
            headers={"Content-Type": "audio/pcm"},
        )
    assert response.status_code == 401


async def test_model_still_loading_is_503_with_retry_after():
    """While a non-default model loads, it answers 503 and the default serves."""
    gate = threading.Event()
    backends = FakeBackends(gates={"parakeet-v3": gate})
    async with running_app(make_settings(), backends) as (app, client):
        await wait_ready(client)
        loading = await transcribe(client, "parakeet-v3")
        assert loading.status_code == 503
        assert loading.json() == {"detail": "Model parakeet-v3 is still loading"}
        assert loading.headers["retry-after"] == "5"
        assert (await transcribe(client)).status_code == 200
        models = (await client.get("/v1/models", headers=AUTH)).json()["models"]
        statuses = {model["id"]: model["status"] for model in models}
        assert statuses["parakeet-v3"] == "loading"
        assert statuses["gigaam-v3"] == "ready"
        gate.set()
        await wait_loaded(app)
        assert (await transcribe(client, "parakeet-v3")).status_code == 200


async def test_failed_model_is_503_and_the_others_keep_serving():
    """A model that failed to load answers 503 with the error; health stays ok."""
    backends = FakeBackends(failures={"parakeet-v3": OSError("disk full")})
    async with running_app(make_settings(), backends) as (app, client):
        await wait_ready(client)
        await wait_loaded(app)
        failed = await transcribe(client, "parakeet-v3")
        assert failed.status_code == 503
        assert failed.json() == {
            "detail": "Model parakeet-v3 failed to load: OSError: disk full"
        }
        assert "retry-after" not in failed.headers
        assert (await transcribe(client, "gigaam-multilingual")).status_code == 200
        health = await client.get("/health")
        assert health.status_code == 200
        statuses = {
            m["id"]: (m["status"], m["device"]) for m in health.json()["models"]
        }
        assert statuses["parakeet-v3"] == ("failed", None)
        assert "disk full" not in health.text  # errors only behind the API key
        models = (await client.get("/v1/models", headers=AUTH)).json()["models"]
        parakeet = next(model for model in models if model["id"] == "parakeet-v3")
        assert parakeet["status"] == "failed"
        assert parakeet["error"] == "OSError: disk full"


async def test_failed_punctuator_fails_only_the_punctuated_model():
    """Without the punctuation model, gigaam-multilingual still serves."""
    backends = FakeBackends(failures={"xlmr-pcs": RuntimeError("bad onnx")})
    async with running_app(make_settings(), backends) as (app, client):
        await wait_loaded(app)
        assert (await transcribe(client, "gigaam-multilingual")).status_code == 200
        response = await transcribe(client, "gigaam-multilingual-punct")
    assert response.status_code == 503
    assert "RuntimeError: bad onnx" in response.json()["detail"]


async def test_failed_default_model_makes_health_503():
    """The service is unhealthy when the default model failed, others still serve."""
    backends = FakeBackends(failures={"gigaam-v3": OSError("no network")})
    async with running_app(make_settings(), backends) as (app, client):
        await wait_loaded(app)
        health = await client.get("/health")
        assert health.status_code == 503
        assert health.json()["status"] == "failed"
        assert (await transcribe(client)).status_code == 503
        assert (await transcribe(client, "parakeet-v3")).status_code == 200


async def test_models_endpoint_lists_the_enabled_models(client):
    """GET /v1/models: default, labels, descriptions, languages, device, status."""
    response = await client.get("/v1/models", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["default"] == "gigaam-v3"
    assert [model["id"] for model in body["models"]] == [
        "gigaam-v3",
        "parakeet-v3",
        "gigaam-multilingual",
        "gigaam-multilingual-punct",
    ]
    first = body["models"][0]
    assert first == {
        "id": "gigaam-v3",
        "label": "Русский — GigaAM v3",
        "description": (
            "Лучшее качество для русской речи, с пунктуацией и заглавными буквами. "
            "Английскую речь не распознаёт."
        ),
        "languages": ["ru"],
        "punctuation": True,
        "device": "cpu",
        "status": "ready",
        "error": None,
    }
    by_id = {model["id"]: model for model in body["models"]}
    assert by_id["parakeet-v3"]["languages"][:2] == ["ru", "en"]
    assert len(by_id["parakeet-v3"]["languages"]) == 25
    assert by_id["gigaam-multilingual"]["punctuation"] is False
    assert by_id["gigaam-multilingual-punct"]["punctuation"] is True
    for model in body["models"]:
        assert model["label"]
        assert model["description"]


async def test_models_endpoint_requires_the_api_key(client):
    """GET /v1/models is behind the Bearer key."""
    response = await client.get("/v1/models")
    assert response.status_code == 401


async def test_models_endpoint_follows_asr_models_order():
    """The list keeps the ASR_MODELS order and marks the configured default."""
    settings = make_settings(
        models=("parakeet-v3", "gigaam-v3"), default_model="parakeet-v3"
    )
    async with running_app(settings) as (app, client):
        await wait_loaded(app)
        body = (await client.get("/v1/models", headers=AUTH)).json()
        response = await transcribe(client)
    assert body["default"] == "parakeet-v3"
    assert [model["id"] for model in body["models"]] == ["parakeet-v3", "gigaam-v3"]
    assert response.json()["model_id"] == "parakeet-v3"


@pytest.mark.parametrize(
    ("raw", "text"),
    [
        ("Это сутки<unk>", "Это сутки"),
        ("  hello \n  world ", "hello world"),
        (".", ""),
        ("<unk>", ""),
        (" , . ", ""),
    ],
)
async def test_text_is_cleaned(raw, text):
    """<...> tokens and extra whitespace are removed; punctuation alone is ""."""

    class Raw(FakeEngine):
        def transcribe_batch(self, wavs):
            return [raw for _ in wavs]

    backends = FakeBackends({"gigaam-v3": Raw()})
    async with running_app(make_settings(models=("gigaam-v3",)), backends) as (
        _app,
        client,
    ):
        await wait_ready(client)
        response = await transcribe(client)
    assert response.json()["text"] == text


async def test_empty_text_is_not_punctuated():
    """The punctuator is not called for an empty recognition."""

    class Silent(FakeEngine):
        def transcribe_batch(self, wavs):
            return [" . " for _ in wavs]

    backends = FakeBackends({"gigaam-multilingual": Silent()})
    settings = make_settings(
        models=("gigaam-multilingual-punct",),
        default_model="gigaam-multilingual-punct",
    )
    async with running_app(settings, backends) as (app, client):
        await wait_loaded(app)
        response = await transcribe(client)
    assert response.json()["text"] == ""
    assert backends.punctuated == []


async def test_punctuated_text_is_cleaned_again():
    """The punctuator's output gets the same clean-up."""
    backends = FakeBackends(punctuate=lambda text: f"  {text.capitalize()}.  ")
    settings = make_settings(
        models=("gigaam-multilingual-punct",),
        default_model="gigaam-multilingual-punct",
    )
    async with running_app(settings, backends) as (app, client):
        await wait_loaded(app)
        response = await transcribe(client)
    assert response.json()["text"] == "Gigaam-multilingual 16000."


async def test_punctuator_error_is_500():
    """An exception in the punctuator fails the request, not the service."""

    def broken(text):
        raise RuntimeError("boom")

    backends = FakeBackends(punctuate=broken)
    async with running_app(make_settings(), backends) as (app, client):
        await wait_loaded(app)
        response = await transcribe(client, "gigaam-multilingual-punct")
        assert response.status_code == 500
        assert (await transcribe(client, "gigaam-multilingual")).status_code == 200


async def test_each_model_has_its_own_queue():
    """A busy model does not delay requests to another model."""
    gate = threading.Event()
    slow = FakeEngine(gate=gate, text="slow")
    backends = FakeBackends({"parakeet-v3": slow})
    async with running_app(make_settings(), backends) as (app, client):
        await wait_loaded(app)
        blocked = asyncio.create_task(transcribe(client, "parakeet-v3"))
        await wait_until(slow.entered.is_set)
        quick = await transcribe(client, "gigaam-v3")
        assert quick.status_code == 200
        assert not blocked.done()
        gate.set()
        assert (await blocked).status_code == 200
