"""LiveKit STT plugin for the self-hosted GigaAM ASR service (``asr/``).

The ASR service is not streaming: it transcribes one phrase (16 kHz mono, at
most ~30 s) per ``POST /v1/transcribe`` request. This plugin therefore cuts
each participant's audio into phrases itself, with the shared Silero VAD (the
same approach as ``voxtral_vllm_stt``):

- the audio of every VAD window is kept, with a short pre-roll before the
  detected start of speech and a short tail after its end;
- a phrase longer than ``GIGAAM_MAX_PHRASE_SECONDS`` is cut at the quietest
  window of its last seconds and the speech continues as a new phrase;
- phrases are queued and sent one by one (per participant) as WAV, so a slow
  request never blocks the VAD; the number of concurrent requests per job
  process is bounded by ``GIGAAM_MAX_CONCURRENCY``;
- each non-empty result is emitted as a FINAL_TRANSCRIPT whose metadata holds
  the wall-clock bounds of the phrase (``wall_start`` / ``wall_end``, epoch
  seconds) and the model that recognized it (``model_id`` / ``model_label``,
  as answered by the ASR service). A phrase that still fails after the
  retries is logged and dropped: the stream, hence the participant session,
  keeps running;
- with a ``model_resolver`` (the model chosen for the room, see
  ``transcription_settings``) every request asks for that model
  (``?model=<id>``); without one, or when it resolves to None, the ASR service
  uses its default model. A model the service rejects (400, e.g. no longer
  enabled) is replaced by the default model for that phrase;
- while the ASR service is unavailable (503, e.g. its model is still loading
  after a restart, or connection errors) a phrase is retried with backoff for
  up to ``GIGAAM_UNAVAILABLE_RETRY_S`` seconds; the following phrases wait in
  the (bounded) queue.

Environment: ``ASR_BASE_URL`` (default ``http://asr:8000``), ``ASR_API_KEY``,
``GIGAAM_MAX_PHRASE_SECONDS`` (20), ``GIGAAM_REQUEST_TIMEOUT_S`` (30),
``GIGAAM_MAX_CONCURRENCY`` (4), ``GIGAAM_UNAVAILABLE_RETRY_S`` (60).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
import weakref
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

import httpx
from livekit import rtc
from livekit.agents import (
    DEFAULT_API_CONNECT_OPTIONS,
    APIConnectionError,
    APIConnectOptions,
    APIStatusError,
    stt,
    utils,
)
from livekit.agents import (
    vad as vad_module,
)
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.agents.utils import is_given

logger = logging.getLogger("gigaam-stt")

SAMPLE_RATE = 16000
LANGUAGE = "ru"
TRANSCRIBE_PATH = "/v1/transcribe"

PREROLL_S = 0.4  # audio kept before the start of speech detected by the VAD
POSTROLL_S = 0.3  # trailing silence kept after the end of speech
PREROLL_BUFFER_S = PREROLL_S + 1.0  # also covers the speech that triggered the VAD
FORCED_CUT_SEARCH_S = 2.0  # a forced cut happens at the quietest window of this tail
MAX_PHRASE_FLOOR_S = 2.0  # lowest accepted GIGAAM_MAX_PHRASE_SECONDS
MAX_QUEUED_PHRASES = 32  # per participant, oldest phrases are dropped beyond it
# a phrase without any VAD window above this probability is not sent (Silero's
# default deactivation threshold: the VAD ends speech below it)
SPEECH_PROBABILITY = 0.35
# retry delays while the ASR service is unavailable: doubled from the first
UNAVAILABLE_FIRST_DELAY_S = 0.5
UNAVAILABLE_MAX_DELAY_S = 4.0
# errors meaning that the ASR service is down or restarting (not slow)
_UNAVAILABLE_ERRORS = (
    httpx.NetworkError,
    httpx.ConnectTimeout,
    httpx.RemoteProtocolError,
)
# time sources of the retries (replaced in tests)
_monotonic = time.monotonic
_sleep = asyncio.sleep

# a window is (audio frame, speech probability given by the VAD)
_Window = tuple[rtc.AudioFrame, float]
# returns the id of the model to ask for, None for the ASR default model
ModelResolver = Callable[[], Awaitable[str | None]]


@dataclass
class _STTOptions:
    base_url: str
    api_key: str | None
    language: str
    max_phrase_s: float
    request_timeout_s: float
    unavailable_retry_s: float


@dataclass(eq=False)
class _Phrase:
    """Audio of one phrase and its wall-clock bounds (epoch seconds)."""

    wall_start: float
    windows: list[_Window] = field(default_factory=list)
    samples: int = 0
    # samples of pre-roll before the speech starts (wall_start)
    speech_offset: int = 0
    wall_end: float | None = None

    def append(self, window: _Window) -> None:
        self.windows.append(window)
        self.samples += window[0].samples_per_channel

    def pop(self) -> _Window:
        window = self.windows.pop()
        self.samples -= window[0].samples_per_channel
        return window

    @property
    def duration(self) -> float:
        return self.samples / SAMPLE_RATE

    def wall_at(self, sample: int) -> float:
        """Wall-clock time of a sample of the phrase, from the speech start."""
        return self.wall_start + max(0, sample - self.speech_offset) / SAMPLE_RATE

    def audio(self) -> rtc.AudioFrame:
        return rtc.combine_audio_frames([frame for frame, _ in self.windows])


@dataclass(frozen=True)
class _Recognition:
    """Text of a phrase and the model that recognized it."""

    text: str
    model_id: str | None = None
    model_label: str | None = None


class _Shared:
    """State shared by all the streams of a job process (one per event loop)."""

    def __init__(self, max_concurrency: int) -> None:
        self.semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self.max_concurrency = max(1, max_concurrency)
        self.client: httpx.AsyncClient | None = None
        # phrases being spoken, queued or recognized
        self.phrases: set[_Phrase] = set()
        self.changed = asyncio.Event()
        # models the ASR service rejected (logged once)
        self.rejected_models: set[str] = set()

    def get_client(self) -> httpx.AsyncClient:
        if self.client is None or self.client.is_closed:
            self.client = httpx.AsyncClient(
                limits=httpx.Limits(
                    max_connections=self.max_concurrency * 2,
                    max_keepalive_connections=self.max_concurrency,
                )
            )
        return self.client

    def track(self, phrase: _Phrase) -> None:
        self.phrases.add(phrase)
        self.changed.set()

    def untrack(self, phrase: _Phrase) -> None:
        self.phrases.discard(phrase)
        self.changed.set()


_shared_by_loop: weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, _Shared] = (
    weakref.WeakKeyDictionary()
)


def _get_shared() -> _Shared:
    loop = asyncio.get_running_loop()
    shared = _shared_by_loop.get(loop)
    if shared is None:
        shared = _Shared(int(os.getenv("GIGAAM_MAX_CONCURRENCY", "4")))
        _shared_by_loop[loop] = shared
    return shared


async def wait_idle(timeout: float, *, started_before: float | None = None) -> bool:
    """Wait until the phrases of this process are recognized (or dropped).

    A phrase being spoken is waited for until the VAD ends it (or it is cut at
    GIGAAM_MAX_PHRASE_SECONDS), then until its final transcript is handed to
    the consumer of the stream: once this returns, the final transcripts of
    the matching phrases were delivered.

    Args:
        timeout: Maximum time to wait, in seconds.
        started_before: When given, only wait for the phrases whose speech
            started at or before this wall-clock time (epoch seconds).

    Returns:
        True when no matching phrase is left, False on timeout.
    """
    shared = _get_shared()

    def busy() -> bool:
        return any(
            started_before is None or phrase.wall_start <= started_before
            for phrase in shared.phrases
        )

    try:
        async with asyncio.timeout(timeout):
            while busy():
                shared.changed.clear()
                await shared.changed.wait()
    except TimeoutError:
        return False
    return True


def _normalize_text(text: str) -> str:
    return " ".join(text.split())


def _optional_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _recognition(payload: object) -> _Recognition | None:
    """Text and model of a transcription response, None when it has no text."""
    text = payload.get("text") if isinstance(payload, dict) else None
    if not isinstance(text, str):
        return None
    return _Recognition(
        text=_normalize_text(text),
        model_id=_optional_str(payload.get("model_id")),
        model_label=_optional_str(payload.get("model_label")),
    )


class STT(stt.STT):
    """Streaming STT cutting phrases with a VAD and sending them to GigaAM."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        vad: vad_module.VAD,
        base_url: NotGivenOr[str] = NOT_GIVEN,
        api_key: NotGivenOr[str] = NOT_GIVEN,
        language: NotGivenOr[str] = NOT_GIVEN,
        max_phrase_s: NotGivenOr[float] = NOT_GIVEN,
        request_timeout_s: NotGivenOr[float] = NOT_GIVEN,
        unavailable_retry_s: NotGivenOr[float] = NOT_GIVEN,
        model_resolver: ModelResolver | None = None,
        http_client: httpx.AsyncClient | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Build the STT.

        Args:
            vad: Shared, prewarmed Voice Activity Detector (required: loading
                Silero here would freeze the event loop once per participant).
            base_url: Base URL of the ASR service. Falls back to $ASR_BASE_URL.
            api_key: Bearer key of the ASR service. Falls back to $ASR_API_KEY.
            language: Language reported on transcripts (GigaAM has no language
                parameter), default "ru".
            max_phrase_s: Longer phrases are cut. Falls back to
                $GIGAAM_MAX_PHRASE_SECONDS, default 20.
            request_timeout_s: Timeout of one ASR request. Falls back to
                $GIGAAM_REQUEST_TIMEOUT_S, default 30.
            unavailable_retry_s: How long a phrase is retried while the ASR
                service is unavailable (503, connection errors). Falls back to
                $GIGAAM_UNAVAILABLE_RETRY_S, default 60.
            model_resolver: Returns the id of the model to ask for (the model
                chosen for the room), None for the default model of the ASR
                service; called before every request.
            http_client: Optional HTTP client (tests); by default a client is
                shared by all the streams of the job process.
            clock: Wall-clock source in epoch seconds (tests).
        """
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=True, interim_results=False, offline_recognize=False
            )
        )
        if vad is None:
            raise ValueError("the gigaam STT requires a VAD (ENABLE_SILERO_VAD=true)")
        self._vad = vad
        self._http_client = http_client
        self._clock = clock
        self._model_resolver = model_resolver

        resolved_max_phrase = (
            max_phrase_s
            if is_given(max_phrase_s)
            else float(os.environ.get("GIGAAM_MAX_PHRASE_SECONDS", "20"))
        )
        self._opts = _STTOptions(
            base_url=(
                base_url
                if is_given(base_url)
                else os.environ.get("ASR_BASE_URL", "http://asr:8000")
            ).rstrip("/"),
            api_key=api_key if is_given(api_key) else os.environ.get("ASR_API_KEY"),
            language=language if is_given(language) else LANGUAGE,
            max_phrase_s=max(MAX_PHRASE_FLOOR_S, resolved_max_phrase),
            request_timeout_s=(
                request_timeout_s
                if is_given(request_timeout_s)
                else float(os.environ.get("GIGAAM_REQUEST_TIMEOUT_S", "30"))
            ),
            unavailable_retry_s=max(
                0.0,
                unavailable_retry_s
                if is_given(unavailable_retry_s)
                else float(os.environ.get("GIGAAM_UNAVAILABLE_RETRY_S", "60")),
            ),
        )
        self._streams: weakref.WeakSet[SpeechStream] = weakref.WeakSet()

    @property
    def model(self) -> str:
        """Return the model family served by the ASR service."""
        return "gigaam-multilingual"

    @property
    def provider(self) -> str:
        """Return the provider identifier."""
        return "gigaam"

    async def _recognize_impl(self, *_args, **_kwargs) -> stt.SpeechEvent:
        raise NotImplementedError("the gigaam STT only supports streaming recognition")

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> SpeechStream:
        """Open a new streaming recognition stream."""
        s = SpeechStream(
            stt=self,
            conn_options=conn_options,
            language=language if is_given(language) else self._opts.language,
        )
        self._streams.add(s)
        return s


class SpeechStream(stt.RecognizeStream):
    """Cut the participant's audio into phrases and transcribe them."""

    def __init__(
        self,
        *,
        stt: STT,
        conn_options: APIConnectOptions,
        language: str,
    ) -> None:
        """Init the speech stream."""
        super().__init__(stt=stt, conn_options=conn_options, sample_rate=SAMPLE_RATE)
        self._opts = stt._opts
        self._vad = stt._vad
        self._http_client = stt._http_client
        self._clock = stt._clock
        self._model_resolver = stt._model_resolver
        self._language = language
        self._shared: _Shared | None = None
        self._preroll: deque[_Window] = deque()
        self._preroll_samples = 0
        self._phrase: _Phrase | None = None
        self._phrase_q: asyncio.Queue[_Phrase | None] = asyncio.Queue()
        # recognized phrases whose final transcript the consumer has not taken
        self._delivering: dict[str, _Phrase] = {}

    async def __anext__(self) -> stt.SpeechEvent:
        """Next event; a phrase is done once its final transcript is taken."""
        ev = await super().__anext__()
        if (
            ev.type == stt.SpeechEventType.FINAL_TRANSCRIPT
            and (phrase := self._delivering.pop(ev.request_id, None)) is not None
        ):
            # the consumer handles the final before the loop wakes wait_idle up
            self._get_shared().untrack(phrase)
        return ev

    @utils.log_exceptions(logger=logger)
    async def _run(self) -> None:
        self._shared = _get_shared()
        vad_stream = self._vad.stream()

        async def input_task() -> None:
            async for data in self._input_ch:
                if isinstance(data, self._FlushSentinel):
                    # phrases end on the silence detected by the VAD
                    continue
                vad_stream.push_frame(data)
            vad_stream.end_input()

        async def vad_task() -> None:
            async for ev in vad_stream:
                if ev.type == vad_module.VADEventType.INFERENCE_DONE:
                    self._on_inference_done(ev)
                elif ev.type == vad_module.VADEventType.START_OF_SPEECH:
                    self._on_start_of_speech(ev)
                elif ev.type == vad_module.VADEventType.END_OF_SPEECH:
                    self._on_end_of_speech(ev)
            # the input ended in the middle of a phrase: keep what was said
            if (phrase := self._phrase) is not None:
                self._phrase = None
                self._close_phrase(phrase, phrase.wall_at(phrase.samples))
                self._send_event(stt.SpeechEventType.END_OF_SPEECH)

        tasks = [
            asyncio.create_task(input_task(), name="gigaam_input"),
            asyncio.create_task(vad_task(), name="gigaam_vad"),
        ]
        pipeline_t = asyncio.create_task(self._phrase_pipeline(), name="gigaam_asr")
        try:
            await asyncio.gather(*tasks)
            # end of stream: the pipeline finishes the queued phrases first
            self._phrase_q.put_nowait(None)
            await pipeline_t
        except (APIStatusError, APIConnectionError, asyncio.CancelledError):
            raise
        except Exception as exc:
            logger.exception("gigaam stream failed")
            raise APIConnectionError() from exc
        finally:
            await utils.aio.cancel_and_wait(*tasks, pipeline_t)
            self._discard_pending()
            await vad_stream.aclose()

    def _send_event(self, event_type: stt.SpeechEventType) -> None:
        self._event_ch.send_nowait(stt.SpeechEvent(type=event_type))

    def _on_inference_done(self, ev: vad_module.VADEvent) -> None:
        max_samples = int(self._opts.max_phrase_s * SAMPLE_RATE)
        for frame in ev.frames:
            window = (frame, ev.probability)
            if self._phrase is None:
                self._push_preroll(window)
                continue
            if self._phrase.samples + frame.samples_per_channel > max_samples:
                self._force_cut()
            self._phrase.append(window)

    def _push_preroll(self, window: _Window) -> None:
        self._preroll.append(window)
        self._preroll_samples += window[0].samples_per_channel
        max_samples = int(PREROLL_BUFFER_S * SAMPLE_RATE)
        while (
            self._preroll
            and self._preroll_samples - self._preroll[0][0].samples_per_channel
            >= max_samples
        ):
            self._preroll_samples -= self._preroll.popleft()[0].samples_per_channel

    def _on_start_of_speech(self, ev: vad_module.VADEvent) -> None:
        if self._phrase is not None:
            return
        phrase = _Phrase(
            wall_start=self._clock() - ev.speech_duration - ev.inference_duration
        )
        # take the speech that triggered the VAD plus the pre-roll
        wanted = int((ev.speech_duration + PREROLL_S) * SAMPLE_RATE)
        windows: list[_Window] = []
        taken = 0
        while self._preroll and taken < wanted:
            window = self._preroll.pop()
            windows.append(window)
            taken += window[0].samples_per_channel
        self._preroll.clear()
        self._preroll_samples = 0
        for window in reversed(windows):
            phrase.append(window)
        phrase.speech_offset = max(
            0, phrase.samples - int(ev.speech_duration * SAMPLE_RATE)
        )
        self._phrase = phrase
        self._get_shared().track(phrase)
        self._send_event(stt.SpeechEventType.START_OF_SPEECH)

    def _on_end_of_speech(self, ev: vad_module.VADEvent) -> None:
        phrase = self._phrase
        if phrase is None:
            return
        self._phrase = None
        wall_end = self._clock() - ev.silence_duration - ev.inference_duration
        # drop the trailing silence beyond POSTROLL_S (it becomes pre-roll)
        to_trim = int(max(0.0, ev.silence_duration - POSTROLL_S) * SAMPLE_RATE)
        trimmed: list[_Window] = []
        while (
            len(phrase.windows) > 1
            and to_trim >= phrase.windows[-1][0].samples_per_channel
        ):
            window = phrase.pop()
            to_trim -= window[0].samples_per_channel
            trimmed.append(window)
        for window in reversed(trimmed):
            self._push_preroll(window)
        self._close_phrase(phrase, max(wall_end, phrase.wall_start))
        self._send_event(stt.SpeechEventType.END_OF_SPEECH)

    def _force_cut(self) -> None:
        """Cut the current phrase at its quietest window near the end."""
        phrase = self._phrase
        if phrase is None:
            return
        search_samples = int(
            min(FORCED_CUT_SEARCH_S, self._opts.max_phrase_s / 2) * SAMPLE_RATE
        )
        # first index of the search area (never the first window)
        first = len(phrase.windows)
        tail = 0
        while first > 1 and tail < search_samples:
            first -= 1
            tail += phrase.windows[first][0].samples_per_channel
        cut = 1 + min(
            range(first, len(phrase.windows)),
            key=lambda i: (phrase.windows[i][1], -i),
        )
        head, rest = phrase.windows[:cut], phrase.windows[cut:]
        cut_sample = sum(frame.samples_per_channel for frame, _ in head)
        cut_wall = phrase.wall_at(cut_sample)

        phrase.windows = head
        phrase.samples = cut_sample
        following = _Phrase(wall_start=cut_wall)
        for window in rest:
            following.append(window)
        self._phrase = following
        self._get_shared().track(following)
        logger.debug("phrase cut after %.1fs of continuous speech", phrase.duration)
        self._close_phrase(phrase, cut_wall)

    def _get_shared(self) -> _Shared:
        if self._shared is None:
            self._shared = _get_shared()
        return self._shared

    def _close_phrase(self, phrase: _Phrase, wall_end: float) -> None:
        phrase.wall_end = wall_end
        if not any(p >= SPEECH_PROBABILITY for _, p in phrase.windows):
            # e.g. the trailing silence left after a forced cut
            self._get_shared().untrack(phrase)
            return
        while self._phrase_q.qsize() >= MAX_QUEUED_PHRASES:
            dropped = self._phrase_q.get_nowait()
            if dropped is not None:
                logger.warning(
                    "ASR is lagging behind: dropping a queued phrase of %.1fs",
                    dropped.duration,
                )
                self._get_shared().untrack(dropped)
        self._phrase_q.put_nowait(phrase)

    def _discard_pending(self) -> None:
        shared = self._get_shared()
        if self._phrase is not None:
            shared.untrack(self._phrase)
            self._phrase = None
        while not self._phrase_q.empty():
            phrase = self._phrase_q.get_nowait()
            if phrase is not None:
                shared.untrack(phrase)
        while self._delivering:
            shared.untrack(self._delivering.popitem()[1])

    async def _phrase_pipeline(self) -> None:
        while True:
            phrase = await self._phrase_q.get()
            if phrase is None:
                return
            delivering = False
            try:
                delivering = await self._recognize_phrase(phrase)
            except Exception:
                # one bad phrase must not stop the transcription of the others
                logger.exception("failed to transcribe a phrase, dropping it")
            finally:
                if not delivering:
                    self._get_shared().untrack(phrase)

    async def _recognize_phrase(self, phrase: _Phrase) -> bool:
        """Transcribe a phrase; True when its final transcript was emitted."""
        audio = phrase.audio()
        request_id = uuid.uuid4().hex
        result = await self._transcribe(audio.to_wav_bytes(), request_id)
        if result is None:
            return False
        text = result.text
        wall_end = phrase.wall_end if phrase.wall_end is not None else self._clock()
        if text:
            start = self.start_time_offset + phrase.wall_start - self.start_time
            end = self.start_time_offset + wall_end - self.start_time
            self._delivering[request_id] = phrase
            self._event_ch.send_nowait(
                stt.SpeechEvent(
                    type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                    request_id=request_id,
                    alternatives=[
                        stt.SpeechData(
                            language=self._language,
                            text=text,
                            start_time=max(0.0, start),
                            end_time=max(0.0, start, end),
                            metadata={
                                "wall_start": phrase.wall_start,
                                "wall_end": wall_end,
                                "model_id": result.model_id,
                                "model_label": result.model_label,
                            },
                        )
                    ],
                )
            )
        self._event_ch.send_nowait(
            stt.SpeechEvent(
                type=stt.SpeechEventType.RECOGNITION_USAGE,
                request_id=request_id,
                recognition_usage=stt.RecognitionUsage(audio_duration=audio.duration),
            )
        )
        return bool(text)

    async def _resolve_model(self) -> str | None:
        if self._model_resolver is None:
            return None
        try:
            return await self._model_resolver()
        except Exception:
            logger.exception("could not resolve the recognition model, using default")
            return None

    def _request_headers(self, request_id: str) -> dict[str, str]:
        headers = {"Content-Type": "audio/wav", "X-Request-Id": request_id}
        if self._opts.api_key:
            headers["Authorization"] = f"Bearer {self._opts.api_key}"
        return headers

    def _log_rejected_model(self, model: str, response: httpx.Response) -> None:
        rejected = self._get_shared().rejected_models
        if model not in rejected:
            rejected.add(model)
            logger.warning(
                "the ASR service rejected the model %s (HTTP %d: %r), "
                "using its default model",
                model,
                response.status_code,
                response.text[:200],
            )

    async def _transcribe(self, wav: bytes, request_id: str) -> _Recognition | None:
        """POST one phrase; return its text and model, None once retries failed.

        Network errors, 408/429 and 5xx are retried ``max_retry`` times. While
        the ASR service is unavailable (503, connection errors) the phrase is
        retried with backoff for up to ``unavailable_retry_s`` seconds. The
        model is resolved again before every attempt; when the service rejects
        it (400) the phrase is sent again at once for the default model.
        """
        shared = self._get_shared()
        client = self._http_client or shared.get_client()
        headers = self._request_headers(request_id)
        timeout = httpx.Timeout(
            self._opts.request_timeout_s, connect=self._conn_options.timeout
        )
        attempts = self._conn_options.max_retry + 1
        started = _monotonic()
        unavailable_delay = UNAVAILABLE_FIRST_DELAY_S
        failures = 0
        default_model = False  # the model of the room was rejected
        while True:
            retryable = unavailable = False
            model = None if default_model else await self._resolve_model()
            try:
                async with shared.semaphore:
                    response = await client.post(
                        f"{self._opts.base_url}{TRANSCRIBE_PATH}",
                        params={"model": model} if model else None,
                        content=wav,
                        headers=headers,
                        timeout=timeout,
                    )
                if response.status_code == httpx.codes.OK:
                    if (result := _recognition(response.json())) is not None:
                        return result
                    error = f"unexpected response: {response.text[:200]!r}"
                elif model and response.status_code == httpx.codes.BAD_REQUEST:
                    # e.g. a model removed from ASR_MODELS since it was chosen
                    self._log_rejected_model(model, response)
                    default_model = True
                    continue
                else:
                    error = f"HTTP {response.status_code}: {response.text[:200]!r}"
                    unavailable = (
                        response.status_code == httpx.codes.SERVICE_UNAVAILABLE
                    )
                    retryable = response.status_code in (408, 429) or (
                        response.status_code >= httpx.codes.INTERNAL_SERVER_ERROR
                    )
            except _UNAVAILABLE_ERRORS as exc:
                error, unavailable = repr(exc), True
            except httpx.TransportError as exc:
                error, retryable = repr(exc), True
            except (httpx.HTTPError, ValueError) as exc:
                error = repr(exc)

            if unavailable:
                # the service is (re)starting: retry for a while
                delay = unavailable_delay
                unavailable_delay = min(UNAVAILABLE_MAX_DELAY_S, delay * 2)
                elapsed = _monotonic() - started
                give_up = elapsed + delay > self._opts.unavailable_retry_s
                progress = f"ASR service unavailable for {elapsed:.0f}s"
            else:
                failures += 1
                delay = 0.1 if failures == 1 else self._conn_options.retry_interval
                give_up = not retryable or failures == attempts
                progress = f"attempt {failures}/{attempts}"
            if give_up:
                logger.error(
                    "ASR request %s failed (%s), dropping the phrase: %s",
                    request_id,
                    progress,
                    error,
                )
                return None
            logger.warning(
                "ASR request %s failed (%s): %s; retrying in %.1fs",
                request_id,
                progress,
                error,
                delay,
            )
            await _sleep(delay)
