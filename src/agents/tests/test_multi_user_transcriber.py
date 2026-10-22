"""Tests of the multi-user transcriber wiring (gigaam provider, storage)."""

import asyncio
import functools
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from livekit import rtc
from livekit.agents import AgentSession
from livekit.agents.voice import io

import gigaam_stt
import multi_user_transcriber as mut
from tests.conftest import (
    WINDOW,
    FakeS3,
    FakeVAD,
    RecordingHook,
    silence,
    to_frames,
    tone,
)
from transcript_store import AgentHook, StoreConfig, TranscriptStore, parse_timestamp
from transcription_control import sign_transcription

T0 = 1_792_404_000.0
AGENT_TOKEN = "agent-token"


def test_gigaam_provider_requires_the_vad(monkeypatch, fake_vad):
    """STT_PROVIDER=gigaam builds the GigaAM STT around the shared VAD."""
    monkeypatch.setattr(mut, "STT_PROVIDER", "gigaam")

    engine = mut.create_stt_provider(vad=fake_vad)

    assert isinstance(engine, gigaam_stt.STT)
    assert engine._vad is fake_vad
    with pytest.raises(ValueError, match="ENABLE_SILERO_VAD"):
        mut.create_stt_provider(vad=None)


async def test_gigaam_provider_asks_for_the_model_of_the_room(monkeypatch, fake_vad):
    """The resolver of the room model reaches the GigaAM STT."""
    monkeypatch.setattr(mut, "STT_PROVIDER", "gigaam")

    async def room_model() -> str | None:
        return "gigaam-multilingual-punct"

    engine = mut.create_stt_provider(vad=fake_vad, model_resolver=room_model)

    assert engine._model_resolver is room_model
    assert mut.create_stt_provider(vad=fake_vad)._model_resolver is None


class FrameInput(io.AudioInput):
    """Audio input replaying frames (faster than real time)."""

    def __init__(self, frames):
        """Replay ``frames`` then stay silent."""
        super().__init__(label="test")
        self._frames = iter(frames)

    async def __anext__(self) -> rtc.AudioFrame:
        """Next frame."""
        frame = next(self._frames, None)
        if frame is None:
            frame = to_frames(silence(1))[0]
            await asyncio.sleep(0.02)
        await asyncio.sleep(0.002)
        return frame


async def test_transcriber_reports_finals_and_keeps_publishing(monkeypatch):
    """Finals reach the storage callback and the session (live subtitles)."""
    stt_vad = FakeVAD()

    def asr(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "привет мир"})

    monkeypatch.setattr(
        mut,
        "create_stt_provider",
        lambda vad=None, model_resolver=None: gigaam_stt.STT(
            vad=stt_vad,
            base_url="http://asr.test",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(asr)),
            clock=stt_vad.clock,
        ),
    )
    reported = []
    agent = mut.Transcriber(
        participant_identity="u1",
        vad=stt_vad,
        on_final=lambda text, start, end, **_: reported.append((text, start, end)),
    )
    session = AgentSession(vad=FakeVAD())
    transcribed = []
    session.on("user_input_transcribed", transcribed.append)
    session.input.audio = FrameInput(
        to_frames(np.concatenate([silence(32), tone(64), silence(32)]))
    )

    await session.start(agent=agent, record=False)
    try:
        for _ in range(500):
            if reported and transcribed:
                break
            await asyncio.sleep(0.01)
    finally:
        await session.aclose()

    ((text, start, end),) = reported
    assert text == "привет мир"
    assert start == pytest.approx(stt_vad.t0 + 1.024, abs=0.05)
    assert end == pytest.approx(stt_vad.t0 + 3.072, abs=0.05)
    assert [ev.transcript for ev in transcribed if ev.is_final] == ["привет мир"]


async def test_transcriber_reports_the_model_of_each_final(monkeypatch):
    """The room model is requested; the model that answered is reported."""
    stt_vad = FakeVAD()
    requested = []

    def asr(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.params.get("model"))
        return httpx.Response(
            200,
            json={
                "text": "hello world",
                "model_id": "gigaam-multilingual-punct",
                "model_label": "GigaAM Multilingual с пунктуацией",
            },
        )

    async def room_model() -> str | None:
        return "gigaam-multilingual-punct"

    monkeypatch.setattr(
        mut,
        "create_stt_provider",
        lambda vad=None, model_resolver=None: gigaam_stt.STT(
            vad=stt_vad,
            base_url="http://asr.test",
            model_resolver=model_resolver,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(asr)),
            clock=stt_vad.clock,
        ),
    )
    reported = []
    agent = mut.Transcriber(
        participant_identity="u1",
        vad=stt_vad,
        on_final=lambda text, start, end, **model: reported.append((text, model)),
        model_resolver=room_model,
    )
    session = AgentSession(vad=FakeVAD())
    session.input.audio = FrameInput(
        to_frames(np.concatenate([silence(32), tone(64), silence(32)]))
    )

    await session.start(agent=agent, record=False)
    try:
        for _ in range(500):
            if reported:
                break
            await asyncio.sleep(0.01)
    finally:
        await session.aclose()

    assert reported == [
        (
            "hello world",
            {
                "model": "gigaam-multilingual-punct",
                "model_label": "GigaAM Multilingual с пунктуацией",
            },
        )
    ]
    assert requested == ["gigaam-multilingual-punct"]


async def test_report_final_never_raises():
    """A failing storage callback does not break the STT pipeline."""

    def broken(*_args, **_kwargs):
        raise RuntimeError("storage is down")

    agent = mut.Transcriber.__new__(mut.Transcriber)
    agent.participant_identity = "u1"
    agent._on_final = broken
    event = SimpleNamespace(
        alternatives=[
            SimpleNamespace(text=" раз ", metadata=None, start_time=1.0, end_time=2.5)
        ]
    )

    agent._report_final(event)  # logged, not raised

    reported = []
    agent._on_final = lambda *args, **kwargs: reported.append((*args, kwargs))
    agent._report_final(event)
    ((text, start, end, model),) = reported
    assert text == "раз"
    assert end - start == pytest.approx(1.5)
    assert model == {"model": None, "model_label": None}


class FakeRoom(rtc.EventEmitter):
    """The parts of rtc.Room used by MultiUserTranscriber."""

    def __init__(self, metadata: str = ""):
        """Init an empty room."""
        super().__init__()
        self.name = "room-uuid"
        self.metadata = metadata
        self.remote_participants = {}


class StubSession:
    """AgentSession stand-in recording how it is closed."""

    def __init__(self, *, drain_error=None, close_error=None):
        """Configure the failures."""
        self.drain_error = drain_error
        self.close_error = close_error
        self.closed = False

    async def drain(self):
        """Drain, possibly failing."""
        if self.drain_error:
            raise self.drain_error

    async def aclose(self):
        """Close, possibly failing."""
        self.closed = True
        if self.close_error:
            raise self.close_error


def transcription_metadata(status: str = "started") -> str:
    """Room metadata with a transcription, signed for the room."""
    return json.dumps(
        {
            "transcription": {
                "id": "t1",
                "status": status,
                "room_name": "Планёрка",
                "started_at": "2026-10-19T10:00:00.000Z",
                "started_by": "Иван",
                "sig": sign_transcription("room-uuid", "t1", AGENT_TOKEN),
            }
        }
    )


@pytest.fixture
def storage_env(monkeypatch):
    """Configure the transcript storage."""
    monkeypatch.setenv("AWS_STORAGE_BUCKET_NAME", "bucket")
    monkeypatch.setenv("TRANSCRIPTION_HOOK_URL", "http://backend.test/hook/")
    monkeypatch.setenv("TRANSCRIPTION_AGENT_TOKEN", AGENT_TOKEN)
    monkeypatch.setenv("TRANSCRIPT_FINALIZE_GRACE_S", "0")


def make_transcriber(room: FakeRoom, fake_s3: FakeS3, hook: RecordingHook):
    """MultiUserTranscriber on a fake job context and storage."""
    ctx = SimpleNamespace(
        room=room,
        job=SimpleNamespace(id="AJ_1", room=SimpleNamespace(name="room-uuid")),
        proc=SimpleNamespace(userdata={}),
    )
    transcriber = mut.MultiUserTranscriber(ctx)
    controller = transcriber._transcription
    hook_client = AgentHook(
        "http://backend.test/hook/", "", transport=httpx.MockTransport(hook)
    )
    controller._store_factory = lambda request: TranscriptStore(
        transcript_id=request.id,
        room_id="room-uuid",
        room_name=request.room_name,
        started_at_ms=request.started_at_ms,
        config=controller._config,
        s3_client=fake_s3,
        hook=hook_client,
    )
    return transcriber


async def settle(transcriber):
    """Wait for the controller's background tasks."""
    while transcriber._transcription._tasks:
        await asyncio.gather(*list(transcriber._transcription._tasks))


def test_storage_is_disabled_without_configuration(monkeypatch):
    """Without bucket and hook the agent only does live subtitles."""
    monkeypatch.delenv("TRANSCRIPTION_HOOK_URL", raising=False)
    ctx = SimpleNamespace(
        room=FakeRoom(), job=SimpleNamespace(room=SimpleNamespace(name="r"))
    )

    transcriber = mut.MultiUserTranscriber(ctx)
    transcriber.start()

    assert transcriber._transcription is None
    assert "room_metadata_changed" not in transcriber.ctx.room._events


async def test_metadata_watcher_opens_and_finalizes(storage_env, fake_s3):
    """Initial metadata opens the store, a stopping update finalizes it."""
    room = FakeRoom(transcription_metadata())
    hook = RecordingHook()
    transcriber = make_transcriber(room, fake_s3, hook)
    participant = SimpleNamespace(identity="u1", name="Иван")

    transcriber.start()
    transcriber.watch_transcription()
    await settle(transcriber)
    store = transcriber._transcription.current
    assert store.transcript_id == "t1"
    now = store.opened_at
    transcriber._on_final_transcript(
        participant,
        "раз",
        now,
        now + 1,
        model="gigaam-v3",
        model_label="Русский — GigaAM v3",
    )
    participant.name = "Иван Петров"  # renamed during the call
    transcriber._on_final_transcript(participant, "два", now + 2, now + 3)
    room.emit(
        "room_metadata_changed", room.metadata, transcription_metadata("stopping")
    )
    await settle(transcriber)

    assert transcriber._transcription.current is None
    assert [p["status"] for p in hook.payloads] == ["ready"]
    document = fake_s3.json("transcriptions/t1/transcript.json")
    assert [(s["name"], s["text"]) for s in document["segments"]] == [
        ("Иван", "раз"),
        ("Иван Петров", "два"),
    ]
    assert [s.get("model") for s in document["segments"]] == ["gigaam-v3", None]
    assert document["models"] == [{"id": "gigaam-v3", "label": "Русский — GigaAM v3"}]
    assert hook.payloads[0]["models"] == ["gigaam-v3"]


async def test_aclose_survives_closed_sessions_and_finalizes(storage_env, fake_s3):
    """Shutdown: closed/failing sessions do not prevent the final transcript."""
    room = FakeRoom(transcription_metadata())
    hook = RecordingHook()
    transcriber = make_transcriber(room, fake_s3, hook)
    transcriber.start()
    transcriber.watch_transcription()
    await settle(transcriber)
    already_closed = StubSession(drain_error=RuntimeError("AgentSession isn't running"))
    failing = StubSession(close_error=RuntimeError("boom"))
    healthy = StubSession()
    transcriber._sessions = {"a": already_closed, "b": failing, "c": healthy}

    await transcriber.aclose()

    assert already_closed.closed and failing.closed and healthy.closed
    assert [p["status"] for p in hook.payloads] == ["ready"]
    assert fake_s3.keys("transcriptions/t1/") == [
        "transcriptions/t1/transcript.json",
        "transcriptions/t1/transcript.md",
    ]
    for event in ("participant_connected", "room_metadata_changed"):
        assert not room._events.get(event)


class FakeAgentSession:
    """AgentSession stand-in recording its options."""

    instances: list["FakeAgentSession"] = []

    def __init__(self, **kwargs):
        """Record the options."""
        self.kwargs = kwargs
        self.closed = False
        FakeAgentSession.instances.append(self)

    async def aclose(self):
        """Close."""
        self.closed = True


class FailingRoomIO:
    """RoomIO stand-in failing to start, to stop _start_session early."""

    def __init__(self, **kwargs):
        """Record the options."""
        self.kwargs = kwargs

    async def start(self):
        """Fail."""
        raise ConnectionError("stop here")


@pytest.mark.parametrize(
    "provider, expected",
    [
        ("gigaam", {"session_close_transcript_timeout": 10.0}),
        ("deepgram", {}),
        ("kyutai", {}),
        ("voxtral-vllm", {}),
    ],
)
async def test_session_close_transcript_timeout_for_gigaam_only(
    monkeypatch, provider, expected
):
    """GigaAM sessions wait longer for the last phrase when a participant leaves."""
    monkeypatch.setattr(mut, "STT_PROVIDER", provider)
    monkeypatch.setattr(mut, "AgentSession", FakeAgentSession)
    monkeypatch.setattr(mut, "RoomIO", FailingRoomIO)
    FakeAgentSession.instances = []
    vad = object()
    transcriber = mut.MultiUserTranscriber.__new__(mut.MultiUserTranscriber)
    transcriber.ctx = SimpleNamespace(
        room=FakeRoom(), proc=SimpleNamespace(userdata={"vad": vad})
    )
    transcriber._sessions = {}

    with pytest.raises(ConnectionError):
        await transcriber._start_session(SimpleNamespace(identity="u1"))

    (session,) = FakeAgentSession.instances
    assert session.kwargs == {"vad": vad, **expected}
    assert session.closed


class StartingSession(FakeAgentSession):
    """AgentSession stand-in that starts and drains."""

    async def start(self, agent):
        """Start the agent."""
        self.agent = agent

    async def drain(self):
        """Nothing to drain."""


class StartingRoomIO(FailingRoomIO):
    """RoomIO stand-in that starts."""

    async def start(self):
        """Start."""


@pytest.mark.parametrize("provider, enabled", [("gigaam", True), ("deepgram", False)])
async def test_sessions_use_the_model_of_the_room(
    storage_env, monkeypatch, provider, enabled
):
    """One cache of the room settings per job, given to every session (gigaam)."""
    monkeypatch.setattr(mut, "STT_PROVIDER", provider)
    monkeypatch.setattr(mut, "AgentSession", StartingSession)
    monkeypatch.setattr(mut, "RoomIO", StartingRoomIO)
    created = []
    monkeypatch.setattr(mut, "Transcriber", lambda **kwargs: created.append(kwargs))
    monkeypatch.setenv("TRANSCRIPTION_SETTINGS_URL", "http://backend.test/settings/")
    ctx = SimpleNamespace(
        room=FakeRoom(),
        job=SimpleNamespace(id="AJ_1", room=SimpleNamespace(name="room-uuid")),
        proc=SimpleNamespace(userdata={"vad": object()}),
    )
    transcriber = mut.MultiUserTranscriber(ctx)
    settings = transcriber._room_settings
    closed = []
    if enabled:
        assert (settings.url, settings.room) == (
            "http://backend.test/settings/",
            "room-uuid",
        )

        async def aclose():
            closed.append(True)

        monkeypatch.setattr(settings, "aclose", aclose)
    else:
        assert settings is None
    transcriber.start()

    await transcriber._start_session(SimpleNamespace(identity="u1"))
    await transcriber._start_session(SimpleNamespace(identity="u2"))
    await transcriber.aclose()

    resolvers = [kwargs["model_resolver"] for kwargs in created]
    if enabled:
        assert resolvers == [settings.asr_model] * 2
        assert closed == [True]
    else:
        assert resolvers == [None, None]


@pytest.mark.parametrize("value, expected", [(None, 10.0), ("4.5", 4.5)])
def test_session_close_transcript_timeout_from_env(value, expected):
    """GIGAAM_SESSION_CLOSE_TIMEOUT_S sets the timeout (default 10 s)."""
    env = {k: v for k, v in os.environ.items() if k != "GIGAAM_SESSION_CLOSE_TIMEOUT_S"}
    if value is not None:
        env["GIGAAM_SESSION_CLOSE_TIMEOUT_S"] = value

    code = "import multi_user_transcriber as m; print(m.GIGAAM_SESSION_CLOSE_TIMEOUT_S)"

    result = subprocess.run(  # noqa: S603 (fixed command)
        [sys.executable, "-c", code],
        cwd=Path(mut.__file__).parent,
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )

    assert float(result.stdout.strip().splitlines()[-1]) == expected


async def test_agent_session_accepts_the_close_transcript_timeout():
    """The installed livekit-agents (1.7.0) has the option, 2 s by default."""
    assert AgentSession(vad=None)._opts.session_close_transcript_timeout == 2.0
    session = AgentSession(vad=None, session_close_transcript_timeout=10.0)
    assert session._opts.session_close_transcript_timeout == 10.0


class HangingInput(io.AudioInput):
    """Audio input replaying frames, then waiting forever (the participant left)."""

    def __init__(self, frames):
        """Replay ``frames``."""
        super().__init__(label="test")
        self._frames = iter(frames)

    async def __anext__(self) -> rtc.AudioFrame:
        """Next frame."""
        frame = next(self._frames, None)
        if frame is None:
            await asyncio.Event().wait()
        await asyncio.sleep(0.002)
        return frame


@pytest.mark.parametrize("close_timeout, kept", [(0.3, False), (5.0, True)])
async def test_last_phrase_kept_when_a_participant_leaves(
    monkeypatch, close_timeout, kept
):
    """Closing waits session_close_transcript_timeout for the last phrase."""
    stt_vad = FakeVAD()

    async def slow_asr(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(1.0)
        return httpx.Response(200, json={"text": "последняя фраза"})

    monkeypatch.setattr(
        mut,
        "create_stt_provider",
        lambda vad=None, model_resolver=None: gigaam_stt.STT(
            vad=stt_vad,
            base_url="http://asr.test",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(slow_asr)),
            clock=stt_vad.clock,
        ),
    )
    reported = []
    agent = mut.Transcriber(
        participant_identity="u1",
        vad=stt_vad,
        on_final=lambda text, start, end, **_: reported.append(text),
    )
    session = AgentSession(
        vad=FakeVAD(), session_close_transcript_timeout=close_timeout
    )
    pcm = np.concatenate([silence(16), tone(48)])  # still speaking when leaving
    session.input.audio = HangingInput(to_frames(pcm))
    await session.start(agent=agent, record=False)
    for _ in range(500):
        if stt_vad.processed_samples >= len(pcm):
            break
        await asyncio.sleep(0.01)

    await mut.MultiUserTranscriber._close_session(None, session)

    assert reported == (["последняя фраза"] if kept else [])


async def test_close_session_after_the_framework_closed_it():
    """drain() of an already closed AgentSession raises RuntimeError: handled."""
    session = AgentSession(vad=None)
    with pytest.raises(RuntimeError):
        await session.drain()

    await mut.MultiUserTranscriber._close_session(None, session)


@pytest.mark.parametrize("status", ["started", "stopping"])
async def test_fresh_job_resumes_the_parts_of_a_dead_job(storage_env, fake_s3, status):
    """A job dispatched again after a crash resumes from the S3 parts.

    ``stopping``: the stop arrived while no transcriber was in the room, the new
    job assembles the saved parts at once.
    """
    now = time.time()
    dead_job = TranscriptStore(
        transcript_id="t1",
        room_id="room-uuid",
        room_name="Планёрка",
        started_at_ms=round((now - 60) * 1000),
        config=StoreConfig.from_env(),
        s3_client=fake_s3,
        hook=None,
        clock=lambda: now - 60,
    )
    dead_job.add_segment(
        identity="u1", name="Иван", start=now - 50, end=now - 48, text="до падения"
    )
    assert await dead_job.flush()  # then the process died, without finalizing
    room = FakeRoom(transcription_metadata(status))
    hook = RecordingHook()
    transcriber = make_transcriber(room, fake_s3, hook)

    transcriber.start()
    transcriber.watch_transcription()
    await settle(transcriber)
    if status == "started":
        store = transcriber._transcription.current
        assert store.transcript_id == "t1"
        participant = SimpleNamespace(identity="u2", name="Мария")
        transcriber._on_final_transcript(
            participant, "после перезапуска", store.opened_at, store.opened_at + 1
        )
        room.emit(
            "room_metadata_changed", room.metadata, transcription_metadata("stopping")
        )
        await settle(transcriber)

    assert transcriber._transcription.current is None
    (payload,) = hook.payloads
    assert payload["status"] == "ready"
    document = fake_s3.json("transcriptions/t1/transcript.json")
    expected = ["до падения"] + (["после перезапуска"] if status == "started" else [])
    assert [s["text"] for s in document["segments"]] == expected
    assert payload["segments_count"] == len(expected)
    await transcriber.aclose()
    assert len(hook.payloads) == 1  # finalized once


@pytest.mark.parametrize("grace, kept", [("0", False), ("5", True)])
async def test_stop_waits_for_the_phrase_being_spoken(
    storage_env, monkeypatch, fake_s3, grace, kept
):
    """A phrase still being spoken at the stop is recognized and kept.

    The finalization waits (TRANSCRIPT_FINALIZE_GRACE_S) for the participant to
    finish the phrase (end of speech by the VAD) and for its recognition.
    """
    monkeypatch.setenv("TRANSCRIPT_FINALIZE_GRACE_S", grace)
    monkeypatch.setattr(mut, "STT_PROVIDER", "gigaam")
    stt_vad = FakeVAD(t0=T0)

    def asr(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"text": "фраза через остановку"})

    monkeypatch.setattr(
        mut,
        "create_stt_provider",
        lambda vad=None, model_resolver=None: gigaam_stt.STT(
            vad=stt_vad,
            base_url="http://asr.test",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(asr)),
            clock=stt_vad.clock,
        ),
    )
    room = FakeRoom(transcription_metadata())
    hook = RecordingHook()
    transcriber = make_transcriber(room, fake_s3, hook)
    controller = transcriber._transcription
    factory = controller._store_factory

    def store_on_the_audio_clock(request):
        store = factory(request)
        store._clock = stt_vad.clock
        store.opened_at = stt_vad.clock()
        return store

    controller._store_factory = store_on_the_audio_clock
    transcriber.start()
    transcriber.watch_transcription()
    await settle(transcriber)

    participant = SimpleNamespace(identity="u1", name="Иван")
    agent = mut.Transcriber(
        participant_identity="u1",
        vad=stt_vad,
        on_final=functools.partial(transcriber._on_final_transcript, participant),
    )
    session = AgentSession(vad=FakeVAD())
    # speech from window 32 to 160, the stop arrives at window 96
    session.input.audio = FrameInput(
        to_frames(np.concatenate([silence(32), tone(128), silence(48)]))
    )
    await session.start(agent=agent, record=False)
    try:
        while stt_vad.processed_samples < 96 * WINDOW:
            await asyncio.sleep(0.001)
        stop_at = stt_vad.clock()
        room.emit(
            "room_metadata_changed", room.metadata, transcription_metadata("stopping")
        )
        await asyncio.wait_for(settle(transcriber), timeout=10)
    finally:
        await session.aclose()

    assert [p["status"] for p in hook.payloads] == ["ready"]
    segments = fake_s3.json("transcriptions/t1/transcript.json")["segments"]
    if not kept:
        assert segments == []
        return
    (segment,) = segments
    assert segment["text"] == "фраза через остановку"
    # started before the stop, ended after it
    assert parse_timestamp(segment["start"]) < stop_at * 1000
    assert parse_timestamp(segment["end"]) > stop_at * 1000 + 1000
