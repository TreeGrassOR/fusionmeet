"""Tests of the recognition model of the room, read from the backend."""

import asyncio
import logging

import httpx
import pytest

from transcription_settings import RoomSettings

URL = "http://backend.test/api/v1.0/transcripts/agent-settings/"


class Clock:
    """Settable monotonic clock."""

    def __init__(self):
        """Start at 0."""
        self.now = 0.0

    def __call__(self) -> float:
        """Current time."""
        return self.now


class FakeBackend:
    """httpx handler standing in for the agent-settings endpoint."""

    def __init__(self, answers=None, default=None):
        """Answer with ``answers`` (responses, exceptions or JSON) in order."""
        self.answers = list(answers or [])
        self.default = default if default is not None else {"asr_model": None}
        self.requests: list[httpx.Request] = []

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        """Record the request and answer it."""
        self.requests.append(request)
        await asyncio.sleep(0)
        answer = self.answers.pop(0) if self.answers else self.default
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json=answer)


def make_settings(backend, clock=None, ttl_s=15.0) -> RoomSettings:
    """Room settings reading the fake backend."""
    return RoomSettings(
        url=URL,
        token="agent-token",
        room="room-uuid",
        ttl_s=ttl_s,
        transport=httpx.MockTransport(backend),
        clock=clock or Clock(),
    )


async def test_reads_the_model_of_the_room_with_the_agent_token():
    """GET ?room=<LiveKit room name> with the Bearer agent token."""
    backend = FakeBackend(default={"asr_model": "gigaam-multilingual-punct"})
    settings = make_settings(backend)

    assert await settings.asr_model() == "gigaam-multilingual-punct"

    (request,) = backend.requests
    assert request.method == "GET"
    assert request.url.copy_with(query=None) == httpx.URL(URL)
    assert request.url.params["room"] == "room-uuid"
    assert request.headers["authorization"] == "Bearer agent-token"
    await settings.aclose()


async def test_value_is_cached_for_the_ttl():
    """The backend is read again only once the TTL has elapsed."""
    backend = FakeBackend(
        answers=[{"asr_model": "gigaam-multilingual-punct"}, {"asr_model": "gigaam-v3"}]
    )
    clock = Clock()
    settings = make_settings(backend, clock)

    assert await settings.asr_model() == "gigaam-multilingual-punct"
    clock.now = 14.9
    assert await settings.asr_model() == "gigaam-multilingual-punct"
    assert len(backend.requests) == 1
    clock.now = 15.0
    assert await settings.asr_model() == "gigaam-v3"
    assert len(backend.requests) == 2
    await settings.aclose()


async def test_null_means_the_asr_default():
    """``null`` (no model chosen, or reset) resolves to None."""
    backend = FakeBackend(
        answers=[{"asr_model": "gigaam-multilingual-punct"}, {"asr_model": None}]
    )
    clock = Clock()
    settings = make_settings(backend, clock)

    assert await settings.asr_model() == "gigaam-multilingual-punct"
    clock.now = 20
    assert await settings.asr_model() is None
    await settings.aclose()


@pytest.mark.parametrize(
    "failure",
    [
        httpx.Response(500, text="boom"),
        httpx.Response(403, json={"detail": "forbidden"}),
        httpx.Response(404, json={"detail": "not found"}),
        httpx.Response(200, text="not json"),
        httpx.Response(200, json=["gigaam-v3"]),
        httpx.Response(200, json={"detail": "no asr_model key"}),
        httpx.Response(200, json={"asr_model": 3}),
        httpx.Response(200, json={"asr_model": ""}),
        httpx.Response(200, json={"asr_model": "x" * 65}),
        httpx.ConnectError("refused"),
        httpx.ReadTimeout("slow"),
    ],
)
async def test_errors_keep_the_last_value(failure, caplog):
    """A failed read keeps the last value and is tried again after the TTL."""
    backend = FakeBackend(
        answers=[
            {"asr_model": "gigaam-multilingual-punct"},
            failure,
            {"asr_model": "gigaam-v3"},
        ]
    )
    clock = Clock()
    settings = make_settings(backend, clock)
    assert await settings.asr_model() == "gigaam-multilingual-punct"

    clock.now = 15
    with caplog.at_level(logging.WARNING, logger="transcription-settings"):
        assert await settings.asr_model() == "gigaam-multilingual-punct"
    assert "keeping the recognition model gigaam-multilingual-punct" in caplog.text
    clock.now = 29  # not hammering a failing backend
    assert await settings.asr_model() == "gigaam-multilingual-punct"
    assert len(backend.requests) == 2

    clock.now = 30
    assert await settings.asr_model() == "gigaam-v3"
    await settings.aclose()


async def test_error_at_the_start_means_the_asr_default():
    """Nothing read yet: the ASR default model (None)."""
    backend = FakeBackend(answers=[httpx.ConnectError("refused")])
    settings = make_settings(backend)

    assert await settings.asr_model() is None
    await settings.aclose()


async def test_concurrent_phrases_share_one_read():
    """Phrases resolving the model at the same time wait for a single read."""
    backend = FakeBackend(default={"asr_model": "gigaam-multilingual-punct"})
    settings = make_settings(backend)

    values = await asyncio.gather(*(settings.asr_model() for _ in range(5)))

    assert values == ["gigaam-multilingual-punct"] * 5
    assert len(backend.requests) == 1
    await settings.aclose()


async def test_cancelled_read_is_done_again():
    """A read cancelled with its phrase does not freeze the value for the TTL."""
    reading = asyncio.Event()
    calls = []

    async def backend(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            reading.set()
            await asyncio.Event().wait()  # until cancelled
        return httpx.Response(200, json={"asr_model": "gigaam-multilingual-punct"})

    settings = make_settings(backend)
    task = asyncio.create_task(settings.asr_model())
    await asyncio.wait_for(reading.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await settings.asr_model() == "gigaam-multilingual-punct"
    assert len(calls) == 2
    await settings.aclose()


async def test_hanging_backend_is_bounded_as_a_whole(caplog):
    """A read that does not end in time keeps the last value."""
    calls = []

    async def backend(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 2:
            await asyncio.Event().wait()  # never answers
        return httpx.Response(200, json={"asr_model": "gigaam-multilingual-punct"})

    clock = Clock()
    settings = RoomSettings(
        url=URL,
        token="agent-token",
        room="room-uuid",
        timeout_s=0.05,
        transport=httpx.MockTransport(backend),
        clock=clock,
    )
    assert await settings.asr_model() == "gigaam-multilingual-punct"

    clock.now = 15
    with caplog.at_level(logging.WARNING, logger="transcription-settings"):
        value = await asyncio.wait_for(settings.asr_model(), timeout=5)
    assert value == "gigaam-multilingual-punct"
    assert "TimeoutError" in caplog.text
    clock.now = 29
    assert await settings.asr_model() == "gigaam-multilingual-punct"
    assert len(calls) == 2
    await settings.aclose()


async def test_model_change_is_logged(caplog):
    """A new model of the room is logged once."""
    backend = FakeBackend(default={"asr_model": "gigaam-multilingual-punct"})
    clock = Clock()
    settings = make_settings(backend, clock, ttl_s=0)

    with caplog.at_level(logging.INFO, logger="transcription-settings"):
        for _ in range(3):
            assert await settings.asr_model() == "gigaam-multilingual-punct"
            clock.now += 1

    assert len(backend.requests) == 3  # TTL 0: read for every phrase
    messages = [r.getMessage() for r in caplog.records]
    assert messages == [
        "recognition model of room room-uuid: gigaam-multilingual-punct"
    ]
    await settings.aclose()


def test_settings_from_environment(monkeypatch, caplog):
    """Enabled by TRANSCRIPTION_SETTINGS_URL, with the agent token."""
    monkeypatch.delenv("TRANSCRIPTION_SETTINGS_URL", raising=False)
    monkeypatch.delenv("TRANSCRIPTION_SETTINGS_TTL_S", raising=False)
    monkeypatch.setenv("TRANSCRIPTION_AGENT_TOKEN", "agent-token")
    assert RoomSettings.from_env("room-uuid") is None

    monkeypatch.setenv("TRANSCRIPTION_SETTINGS_URL", URL)
    settings = RoomSettings.from_env("room-uuid")
    assert (settings.url, settings.room, settings.ttl_s) == (URL, "room-uuid", 15)
    assert settings._token == "agent-token"
    monkeypatch.setenv("TRANSCRIPTION_SETTINGS_TTL_S", "2.5")
    assert RoomSettings.from_env("room-uuid").ttl_s == 2.5
    monkeypatch.setenv("TRANSCRIPTION_SETTINGS_TTL_S", "-1")
    assert RoomSettings.from_env("room-uuid").ttl_s == 0

    monkeypatch.setenv("TRANSCRIPTION_AGENT_TOKEN", "")
    with caplog.at_level(logging.WARNING, logger="transcription-settings"):
        assert RoomSettings.from_env("room-uuid") is None
    assert "TRANSCRIPTION_AGENT_TOKEN is not" in caplog.text
