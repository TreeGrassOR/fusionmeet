"""Tests of the GigaAM STT provider (VAD phrase cutting + ASR client)."""

import asyncio
import logging

import httpx
import numpy as np
import pytest
from livekit.agents import APIConnectOptions, stt

import gigaam_stt
from tests.conftest import (
    SAMPLE_RATE,
    WINDOW_S,
    read_wav,
    silence,
    to_frames,
    tone,
)

FAST = APIConnectOptions(max_retry=2, retry_interval=0.01, timeout=1.0)


class FakeASR:
    """httpx handler standing in for the ASR service."""

    def __init__(self, answers=None, default=None):
        """Answer with ``answers`` (responses or exceptions) in order."""
        self.answers = list(answers or [])
        self.default = default or {"text": "привет мир"}
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Record the request and answer it."""
        self.requests.append(request)
        answer = self.answers.pop(0) if self.answers else self.default
        if isinstance(answer, Exception):
            raise answer
        if isinstance(answer, httpx.Response):
            return answer
        return httpx.Response(200, json={"duration": 1.0, **answer})


class FakeTime:
    """Monotonic clock of the retries, advanced by their sleeps only."""

    def __init__(self):
        """Start at 0."""
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        """Current time."""
        return self.now

    async def sleep(self, delay: float) -> None:
        """Advance the clock instead of waiting."""
        self.sleeps.append(delay)
        self.now += delay
        await asyncio.sleep(0)


@pytest.fixture
def fake_time(monkeypatch):
    """Control the time of the ASR retries."""
    clock = FakeTime()
    monkeypatch.setattr(gigaam_stt, "_monotonic", clock.monotonic)
    monkeypatch.setattr(gigaam_stt, "_sleep", clock.sleep)
    return clock


LOADING = httpx.Response(503, json={"detail": "The model is still loading"})
ONE_PHRASE = np.concatenate([silence(32), tone(64), silence(32)])
TWO_PHRASES = np.concatenate(
    [silence(32), tone(48), silence(40), tone(48), silence(32)]
)


def make_stt(fake_vad, handler, **kwargs) -> gigaam_stt.STT:
    """Build the STT with a mocked ASR and the fake VAD clock."""
    return gigaam_stt.STT(
        vad=fake_vad,
        base_url="http://asr.test:8000/",
        api_key="secret",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        clock=fake_vad.clock,
        **kwargs,
    )


async def run_stream(engine, pcm, sample_rate=SAMPLE_RATE) -> list[stt.SpeechEvent]:
    """Push the audio, end the input and collect every event."""
    stream = engine.stream(conn_options=FAST)
    for frame in to_frames(pcm, sample_rate):
        stream.push_frame(frame)
    stream.end_input()
    events = [ev async for ev in stream]
    await stream.aclose()
    return events


def of_type(events, event_type):
    """Filter events by type."""
    return [ev for ev in events if ev.type == event_type]


async def test_phrase_emits_start_end_and_final_with_wall_times(fake_vad):
    """One phrase: START, END, FINAL with the text and its wall-clock bounds."""
    asr = FakeASR()
    engine = make_stt(fake_vad, asr)
    pcm = np.concatenate([silence(32), tone(64), silence(32)])

    events = await run_stream(engine, pcm)

    types = [ev.type for ev in events]
    assert types.count(stt.SpeechEventType.START_OF_SPEECH) == 1
    assert types.count(stt.SpeechEventType.END_OF_SPEECH) == 1
    assert types.index(stt.SpeechEventType.START_OF_SPEECH) < types.index(
        stt.SpeechEventType.END_OF_SPEECH
    )
    (final,) = of_type(events, stt.SpeechEventType.FINAL_TRANSCRIPT)
    data = final.alternatives[0]
    assert data.text == "привет мир"
    assert data.language == "ru"
    # speech is windows 32..95 of the audio clock
    assert data.metadata["wall_start"] == pytest.approx(
        fake_vad.t0 + 32 * WINDOW_S, abs=WINDOW_S
    )
    assert data.metadata["wall_end"] == pytest.approx(
        fake_vad.t0 + 96 * WINDOW_S, abs=WINDOW_S
    )
    assert 0 <= data.start_time <= data.end_time
    (usage,) = of_type(events, stt.SpeechEventType.RECOGNITION_USAGE)
    assert usage.recognition_usage.audio_duration == pytest.approx(2.784, abs=0.07)


async def test_sends_wav_with_bearer_key(fake_vad):
    """The phrase is POSTed as a 16 kHz mono s16 WAV with the Bearer key."""
    asr = FakeASR()
    engine = make_stt(fake_vad, asr)

    await run_stream(engine, np.concatenate([silence(32), tone(64), silence(32)]))

    (request,) = asr.requests
    assert request.method == "POST"
    assert str(request.url) == "http://asr.test:8000/v1/transcribe"
    assert request.headers["authorization"] == "Bearer secret"
    assert request.headers["content-type"] == "audio/wav"
    channels, width, rate, duration = read_wav(request.content)
    assert (channels, width, rate) == (1, 2, 16000)
    # pre-roll (0.4 s) + speech (2.048 s) + kept tail (0.3 s)
    assert duration == pytest.approx(0.4 + 2.048 + 0.3, abs=0.07)


async def test_resamples_input_to_16k(fake_vad):
    """Audio pushed at 24 kHz reaches the ASR at 16 kHz."""
    asr = FakeASR()
    engine = make_stt(fake_vad, asr)
    pcm16 = np.concatenate([silence(32), tone(64), silence(32)])
    pcm24 = np.repeat(pcm16, 3)[::2]  # crude 16 -> 24 kHz

    events = await run_stream(engine, pcm24, sample_rate=24000)

    assert len(of_type(events, stt.SpeechEventType.FINAL_TRANSCRIPT)) == 1
    assert read_wav(asr.requests[0].content)[2] == 16000


async def test_empty_text_emits_no_final(fake_vad):
    """An empty transcription is not reported as a FINAL transcript."""
    engine = make_stt(fake_vad, FakeASR(default={"text": "  "}))

    events = await run_stream(
        engine, np.concatenate([silence(32), tone(64), silence(32)])
    )

    assert of_type(events, stt.SpeechEventType.FINAL_TRANSCRIPT) == []
    assert len(of_type(events, stt.SpeechEventType.END_OF_SPEECH)) == 1
    assert len(of_type(events, stt.SpeechEventType.RECOGNITION_USAGE)) == 1


async def test_asr_error_drops_phrase_and_stream_continues(fake_vad):
    """A phrase failing after the retries is dropped; the next one works."""
    asr = FakeASR(
        answers=[httpx.Response(500, text="boom")] * 3 + [{"text": "вторая фраза"}]
    )
    engine = make_stt(fake_vad, asr)
    pcm = np.concatenate([silence(32), tone(48), silence(40), tone(48), silence(32)])

    events = await run_stream(engine, pcm)

    assert len(of_type(events, stt.SpeechEventType.START_OF_SPEECH)) == 2
    assert len(of_type(events, stt.SpeechEventType.END_OF_SPEECH)) == 2
    finals = of_type(events, stt.SpeechEventType.FINAL_TRANSCRIPT)
    assert [ev.alternatives[0].text for ev in finals] == ["вторая фраза"]
    # 1 attempt + max_retry (2) for the first phrase, 1 for the second
    assert len(asr.requests) == 4


async def test_unexpected_error_drops_only_that_phrase(fake_vad):
    """An unexpected failure of one phrase does not stop the next ones."""
    asr = FakeASR(answers=[RuntimeError("bug"), {"text": "вторая фраза"}])
    engine = make_stt(fake_vad, asr)
    pcm = np.concatenate([silence(32), tone(48), silence(40), tone(48), silence(32)])

    events = await run_stream(engine, pcm)

    finals = of_type(events, stt.SpeechEventType.FINAL_TRANSCRIPT)
    assert [ev.alternatives[0].text for ev in finals] == ["вторая фраза"]
    assert len(asr.requests) == 2


async def test_client_error_is_not_retried(fake_vad):
    """4xx answers (e.g. 401, 422) drop the phrase at once."""
    asr = FakeASR(answers=[httpx.Response(422, json={"detail": "bad audio"})])
    engine = make_stt(fake_vad, asr)

    events = await run_stream(
        engine, np.concatenate([silence(32), tone(64), silence(32)])
    )

    assert of_type(events, stt.SpeechEventType.FINAL_TRANSCRIPT) == []
    assert len(asr.requests) == 1


async def test_connection_error_is_retried(fake_vad):
    """Network errors are retried before giving up."""
    asr = FakeASR(answers=[httpx.ConnectError("refused")])
    engine = make_stt(fake_vad, asr)

    events = await run_stream(
        engine, np.concatenate([silence(32), tone(64), silence(32)])
    )

    assert len(of_type(events, stt.SpeechEventType.FINAL_TRANSCRIPT)) == 1
    assert len(asr.requests) == 2


async def test_restarting_service_is_waited_for(fake_vad, fake_time, monkeypatch):
    """503 while the model loads (15+ s): the phrase waits for the service."""
    monkeypatch.delenv("GIGAAM_UNAVAILABLE_RETRY_S", raising=False)
    asr = FakeASR(answers=[LOADING] * 8 + [{"text": "дождались"}])
    engine = make_stt(fake_vad, asr)  # max_retry=2: 3 attempts for other errors

    events = await run_stream(engine, ONE_PHRASE)

    finals = of_type(events, stt.SpeechEventType.FINAL_TRANSCRIPT)
    assert [ev.alternatives[0].text for ev in finals] == ["дождались"]
    assert len(asr.requests) == 9
    # backoff doubled from 0.5 s up to 4 s: 23.5 s of unavailability
    assert fake_time.sleeps == [0.5, 1.0, 2.0, 4.0, 4.0, 4.0, 4.0, 4.0]
    assert fake_time.now == pytest.approx(23.5)


@pytest.mark.parametrize(
    "error",
    [
        httpx.ConnectError("[Errno -2] Name or service not known"),
        httpx.ConnectError("[Errno 111] Connection refused"),
        httpx.ConnectTimeout("connect timed out"),
        httpx.RemoteProtocolError("Server disconnected without sending a response"),
        httpx.ReadError("connection reset by peer"),
    ],
)
async def test_connection_errors_wait_for_the_service(fake_vad, fake_time, error):
    """A service down or restarting (connection errors) is waited for as well."""
    asr = FakeASR(answers=[error] * 6 + [{"text": "снова на связи"}])
    engine = make_stt(fake_vad, asr)

    events = await run_stream(engine, ONE_PHRASE)

    finals = of_type(events, stt.SpeechEventType.FINAL_TRANSCRIPT)
    assert [ev.alternatives[0].text for ev in finals] == ["снова на связи"]
    assert len(asr.requests) == 7


async def test_unavailable_service_budget_per_phrase(fake_vad, fake_time, caplog):
    """After GIGAAM_UNAVAILABLE_RETRY_S the phrase is dropped, the next one works."""
    # 18 attempts fit in 60 s: 0 + 0.5 + 1 + 2 + 14 x 4 = 59.5 s
    asr = FakeASR(answers=[LOADING] * 18 + [{"text": "вторая фраза"}])
    engine = make_stt(fake_vad, asr, unavailable_retry_s=60)

    with caplog.at_level(logging.WARNING, logger="gigaam-stt"):
        events = await run_stream(engine, TWO_PHRASES)

    finals = of_type(events, stt.SpeechEventType.FINAL_TRANSCRIPT)
    assert [ev.alternatives[0].text for ev in finals] == ["вторая фраза"]
    assert len(asr.requests) == 19
    assert sum(fake_time.sleeps) == pytest.approx(59.5)
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "ASR service unavailable for 60s" in errors[0]
    assert "dropping the phrase" in errors[0]


async def test_other_errors_keep_their_retry_count(fake_vad, fake_time):
    """503 do not use up the retries of other errors, and the reverse."""
    error = httpx.Response(500, text="boom")
    asr = FakeASR(answers=[LOADING, error, LOADING, LOADING, error, error])
    engine = make_stt(fake_vad, asr)  # max_retry=2

    events = await run_stream(engine, ONE_PHRASE)

    assert of_type(events, stt.SpeechEventType.FINAL_TRANSCRIPT) == []
    assert len(asr.requests) == 6  # dropped at the third 500
    # unavailable: 0.5, 1, 2 s; other errors: 0.1 s then retry_interval
    assert fake_time.sleeps == [0.5, 0.1, 1.0, 2.0, 0.01]


async def test_timeouts_keep_the_retry_count(fake_vad, fake_time):
    """A slow service (read timeout) is not waited for longer than before."""
    asr = FakeASR(answers=[httpx.ReadTimeout("slow")] * 3)
    engine = make_stt(fake_vad, asr)

    events = await run_stream(engine, ONE_PHRASE)

    assert of_type(events, stt.SpeechEventType.FINAL_TRANSCRIPT) == []
    assert len(asr.requests) == 3


async def test_queue_stays_bounded_while_the_service_is_down(
    fake_vad, monkeypatch, caplog
):
    """Phrases queued while the ASR is down are capped, the oldest dropped."""
    monkeypatch.setattr(gigaam_stt, "MAX_QUEUED_PHRASES", 2)
    release = asyncio.Event()
    texts = iter(["первая", "четвёртая", "пятая"])

    async def asr(request: httpx.Request) -> httpx.Response:
        await release.wait()
        return httpx.Response(200, json={"text": next(texts)})

    engine = make_stt(fake_vad, asr)
    stream = engine.stream(conn_options=FAST)
    pcm = np.concatenate(
        [silence(16)] + [np.concatenate([tone(24), silence(24)]) for _ in range(5)]
    )
    for frame in to_frames(pcm):
        stream.push_frame(frame)
    stream.end_input()
    events = []

    async def collect():
        async for ev in stream:
            events.append(ev)
            if len(of_type(events, stt.SpeechEventType.END_OF_SPEECH)) == 5:
                release.set()

    with caplog.at_level(logging.WARNING, logger="gigaam-stt"):
        await asyncio.wait_for(collect(), timeout=10)
    await stream.aclose()

    finals = of_type(events, stt.SpeechEventType.FINAL_TRANSCRIPT)
    assert [ev.alternatives[0].text for ev in finals] == [
        "первая",
        "четвёртая",
        "пятая",
    ]
    assert caplog.text.count("ASR is lagging behind: dropping a queued phrase") == 2


async def test_long_speech_is_cut_at_the_quietest_window(fake_vad):
    """Speech longer than the limit is cut, preferably in a pause."""
    asr = FakeASR(answers=[{"text": "первая часть"}, {"text": "вторая часть"}])
    engine = make_stt(fake_vad, asr, max_phrase_s=2.0)
    # speech: windows 16..55, short dip 56..57 (not a VAD silence), 58..97
    pcm = np.concatenate([silence(16), tone(40), silence(2), tone(40), silence(32)])

    events = await run_stream(engine, pcm)

    assert len(of_type(events, stt.SpeechEventType.START_OF_SPEECH)) == 1
    assert len(of_type(events, stt.SpeechEventType.END_OF_SPEECH)) == 1
    finals = of_type(events, stt.SpeechEventType.FINAL_TRANSCRIPT)
    assert [ev.alternatives[0].text for ev in finals] == [
        "первая часть",
        "вторая часть",
    ]
    first, second = (ev.alternatives[0].metadata for ev in finals)
    t0 = fake_vad.t0
    assert first["wall_start"] == pytest.approx(t0 + 16 * WINDOW_S, abs=WINDOW_S)
    # cut right after the dip, the second part starts where the first ends
    assert first["wall_end"] == pytest.approx(t0 + 58 * WINDOW_S, abs=WINDOW_S)
    assert second["wall_start"] == first["wall_end"]
    assert second["wall_end"] == pytest.approx(t0 + 98 * WINDOW_S, abs=WINDOW_S)
    for request in asr.requests:
        assert read_wav(request.content)[3] <= 2.0


async def test_continuous_speech_is_split_below_the_limit(fake_vad):
    """A long monologue becomes contiguous phrases of at most the limit."""
    asr = FakeASR()
    engine = make_stt(fake_vad, asr, max_phrase_s=2.0)

    events = await run_stream(
        engine, np.concatenate([silence(16), tone(150), silence(32)])
    )

    finals = of_type(events, stt.SpeechEventType.FINAL_TRANSCRIPT)
    assert len(finals) == 3
    bounds = [
        (
            ev.alternatives[0].metadata["wall_start"],
            ev.alternatives[0].metadata["wall_end"],
        )
        for ev in finals
    ]
    for (_, end), (start, _) in zip(bounds, bounds[1:], strict=False):
        assert start == end
    assert bounds[0][0] == pytest.approx(fake_vad.t0 + 16 * WINDOW_S, abs=WINDOW_S)
    assert bounds[-1][1] == pytest.approx(fake_vad.t0 + 166 * WINDOW_S, abs=WINDOW_S)
    assert all(read_wav(r.content)[3] <= 2.0 for r in asr.requests)


async def test_input_ending_mid_phrase_keeps_the_phrase(fake_vad):
    """When the input ends during speech, the phrase is still transcribed."""
    engine = make_stt(fake_vad, FakeASR())

    events = await run_stream(engine, np.concatenate([silence(16), tone(40)]))

    assert len(of_type(events, stt.SpeechEventType.END_OF_SPEECH)) == 1
    assert len(of_type(events, stt.SpeechEventType.FINAL_TRANSCRIPT)) == 1


async def test_wait_idle_tracks_phrases_in_flight(fake_vad):
    """wait_idle waits for queued/in-flight phrases, optionally by start time."""
    release = asyncio.Event()

    async def slow_asr(request: httpx.Request) -> httpx.Response:
        await release.wait()
        return httpx.Response(200, json={"text": "медленно"})

    engine = make_stt(fake_vad, slow_asr)
    stream = engine.stream(conn_options=FAST)
    for frame in to_frames(np.concatenate([silence(32), tone(64), silence(32)])):
        stream.push_frame(frame)

    async def first_end():
        async for ev in stream:
            if ev.type == stt.SpeechEventType.END_OF_SPEECH:
                return

    await asyncio.wait_for(first_end(), timeout=5)
    assert not await gigaam_stt.wait_idle(0.05)
    # the phrase started after t0: nothing to wait for before t0
    assert await gigaam_stt.wait_idle(0.05, started_before=fake_vad.t0)
    release.set()
    # recognized, but its final transcript is not taken by the consumer yet
    assert not await gigaam_stt.wait_idle(0.2)
    received = []

    async def consume():
        async for ev in stream:
            if ev.type == stt.SpeechEventType.FINAL_TRANSCRIPT:
                received.append(ev.alternatives[0].text)

    consumer = asyncio.create_task(consume())
    assert await gigaam_stt.wait_idle(2)
    # the consumer had the final before wait_idle returned
    assert received == ["медленно"]
    stream.end_input()
    await consumer
    await stream.aclose()


async def test_closing_the_stream_releases_undelivered_finals(fake_vad):
    """A final transcript nobody takes does not keep wait_idle busy after close."""
    engine = make_stt(fake_vad, FakeASR())
    stream = engine.stream(conn_options=FAST)
    for frame in to_frames(ONE_PHRASE):
        stream.push_frame(frame)
    async for ev in stream:
        if ev.type == stt.SpeechEventType.END_OF_SPEECH:
            break
    assert not await gigaam_stt.wait_idle(0.3)  # recognized, not taken

    await stream.aclose()

    assert await gigaam_stt.wait_idle(0.5)


async def test_closing_the_stream_releases_pending_phrases(fake_vad):
    """Cancelled phrases do not keep wait_idle busy."""

    async def never(request: httpx.Request) -> httpx.Response:
        await asyncio.Event().wait()
        raise AssertionError

    engine = make_stt(fake_vad, never)
    stream = engine.stream(conn_options=FAST)
    for frame in to_frames(np.concatenate([silence(32), tone(64), silence(32)])):
        stream.push_frame(frame)
    async for ev in stream:
        if ev.type == stt.SpeechEventType.END_OF_SPEECH:
            break
    assert not await gigaam_stt.wait_idle(0.05)

    await stream.aclose()

    assert await gigaam_stt.wait_idle(0.5)


def test_requires_a_vad():
    """The provider refuses to run without a VAD."""
    with pytest.raises(ValueError, match="VAD"):
        gigaam_stt.STT(vad=None)


def test_settings_from_environment(monkeypatch, fake_vad):
    """ASR URL, key and limits come from the environment."""
    monkeypatch.setenv("ASR_BASE_URL", "http://asr:8000/")
    monkeypatch.setenv("ASR_API_KEY", "k")
    monkeypatch.setenv("GIGAAM_MAX_PHRASE_SECONDS", "12")
    monkeypatch.setenv("GIGAAM_REQUEST_TIMEOUT_S", "7")
    monkeypatch.setenv("GIGAAM_UNAVAILABLE_RETRY_S", "90")

    engine = gigaam_stt.STT(vad=fake_vad)

    assert engine._opts.base_url == "http://asr:8000"
    assert engine._opts.api_key == "k"
    assert engine._opts.max_phrase_s == 12
    assert engine._opts.request_timeout_s == 7
    assert engine._opts.unavailable_retry_s == 90
    monkeypatch.delenv("GIGAAM_UNAVAILABLE_RETRY_S")
    assert gigaam_stt.STT(vad=fake_vad)._opts.unavailable_retry_s == 60
    assert engine.provider == "gigaam"
    assert engine.capabilities.streaming


class Resolver:
    """Model of the room, changed by the test between phrases."""

    def __init__(self, *models):
        """Resolve to ``models`` in order, then to the last one."""
        self.models = list(models)
        self.calls = 0

    async def __call__(self) -> str | None:
        """Model id, None for the ASR default model."""
        self.calls += 1
        return self.models.pop(0) if len(self.models) > 1 else self.models[0]


GIGAAM_V3 = {"model_id": "gigaam-v3", "model_label": "Русский — GigaAM v3"}
MULTILINGUAL_PUNCT = {
    "model_id": "gigaam-multilingual-punct",
    "model_label": "GigaAM Multilingual с пунктуацией",
}


async def test_requests_the_model_of_the_room(fake_vad):
    """Each phrase asks for the resolved model; its id and label reach the final."""
    asr = FakeASR(
        answers=[{"text": "раз", **MULTILINGUAL_PUNCT}, {"text": "два", **GIGAAM_V3}]
    )
    resolver = Resolver("gigaam-multilingual-punct", "gigaam-v3")
    engine = make_stt(fake_vad, asr, model_resolver=resolver)

    events = await run_stream(engine, TWO_PHRASES)

    assert [r.url.params.get("model") for r in asr.requests] == [
        "gigaam-multilingual-punct",
        "gigaam-v3",
    ]
    assert asr.requests[0].url.path == "/v1/transcribe"
    finals = of_type(events, stt.SpeechEventType.FINAL_TRANSCRIPT)
    assert [
        (
            ev.alternatives[0].metadata["model_id"],
            ev.alternatives[0].metadata["model_label"],
        )
        for ev in finals
    ] == [
        ("gigaam-multilingual-punct", "GigaAM Multilingual с пунктуацией"),
        ("gigaam-v3", "Русский — GigaAM v3"),
    ]
    assert resolver.calls == 2


@pytest.mark.parametrize("resolver", [None, Resolver(None)])
async def test_default_model_sends_no_model_parameter(fake_vad, resolver):
    """No resolver, or no model chosen for the room: the ASR default model."""
    asr = FakeASR(default={"text": "привет", **GIGAAM_V3})
    engine = make_stt(fake_vad, asr, model_resolver=resolver)

    events = await run_stream(engine, ONE_PHRASE)

    (request,) = asr.requests
    assert "model" not in request.url.params
    assert str(request.url) == "http://asr.test:8000/v1/transcribe"
    (final,) = of_type(events, stt.SpeechEventType.FINAL_TRANSCRIPT)
    assert final.alternatives[0].metadata["model_id"] == "gigaam-v3"


async def test_older_asr_service_reports_no_model(fake_vad):
    """A response without model_id / model_label gives None in the metadata."""
    engine = make_stt(fake_vad, FakeASR(default={"text": "раз", "model_id": 7}))

    events = await run_stream(engine, ONE_PHRASE)

    (final,) = of_type(events, stt.SpeechEventType.FINAL_TRANSCRIPT)
    assert final.alternatives[0].metadata["model_id"] is None
    assert final.alternatives[0].metadata["model_label"] is None


async def test_model_is_resolved_again_on_retries(fake_vad, fake_time):
    """A phrase waiting for the service picks up a model changed meanwhile."""
    asr = FakeASR(answers=[LOADING, {"text": "раз", **GIGAAM_V3}])
    engine = make_stt(
        fake_vad, asr, model_resolver=Resolver("gigaam-multilingual", "gigaam-v3")
    )

    events = await run_stream(engine, ONE_PHRASE)

    assert [r.url.params["model"] for r in asr.requests] == [
        "gigaam-multilingual",
        "gigaam-v3",
    ]
    assert len(of_type(events, stt.SpeechEventType.FINAL_TRANSCRIPT)) == 1


async def test_rejected_model_falls_back_to_the_default(fake_vad, fake_time, caplog):
    """400 for the model of the room: the phrase is sent again without it."""
    rejected = httpx.Response(400, json={"detail": "Unknown model 'whisper'"})
    asr = FakeASR(
        answers=[
            rejected,
            {"text": "раз", **GIGAAM_V3},
            rejected,
            {"text": "два", **GIGAAM_V3},
        ]
    )
    engine = make_stt(fake_vad, asr, model_resolver=Resolver("whisper"))

    with caplog.at_level(logging.WARNING, logger="gigaam-stt"):
        events = await run_stream(engine, TWO_PHRASES)

    assert [r.url.params.get("model") for r in asr.requests] == [
        "whisper",
        None,
        "whisper",
        None,
    ]
    finals = of_type(events, stt.SpeechEventType.FINAL_TRANSCRIPT)
    assert [ev.alternatives[0].text for ev in finals] == ["раз", "два"]
    assert {ev.alternatives[0].metadata["model_id"] for ev in finals} == {"gigaam-v3"}
    assert fake_time.sleeps == []  # sent again at once
    warnings = [r.getMessage() for r in caplog.records if r.name == "gigaam-stt"]
    assert len(warnings) == 1  # once per model
    assert "rejected the model whisper" in warnings[0]


async def test_bad_request_without_model_is_not_retried(fake_vad):
    """A 400 for the default model (e.g. undecodable audio) drops the phrase."""
    asr = FakeASR(answers=[httpx.Response(400, json={"detail": "bad audio"})])
    engine = make_stt(fake_vad, asr, model_resolver=Resolver(None))

    events = await run_stream(engine, ONE_PHRASE)

    assert of_type(events, stt.SpeechEventType.FINAL_TRANSCRIPT) == []
    assert len(asr.requests) == 1


async def test_failing_resolver_uses_the_default_model(fake_vad):
    """A broken resolver never drops the phrase."""

    async def broken() -> str | None:
        raise RuntimeError("bug")

    asr = FakeASR()
    engine = make_stt(fake_vad, asr, model_resolver=broken)

    events = await run_stream(engine, ONE_PHRASE)

    assert "model" not in asr.requests[0].url.params
    assert len(of_type(events, stt.SpeechEventType.FINAL_TRANSCRIPT)) == 1
