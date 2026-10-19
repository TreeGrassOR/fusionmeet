"""Shared fixtures of the agents tests: fake VAD, fake S3, audio helpers."""

import asyncio
import io
import json
import wave

import httpx
import numpy as np
import pytest
from botocore.exceptions import ClientError
from livekit import rtc
from livekit.agents import vad

SAMPLE_RATE = 16000
WINDOW = 512  # samples per VAD window, as Silero at 16 kHz
WINDOW_S = WINDOW / SAMPLE_RATE
TONE_AMPLITUDE = 8000


def tone(windows: int, amplitude: int = TONE_AMPLITUDE) -> np.ndarray:
    """Speech stand-in: a 440 Hz tone lasting ``windows`` VAD windows."""
    t = np.arange(windows * WINDOW) / SAMPLE_RATE
    return (amplitude * np.sin(2 * np.pi * 440 * t)).astype(np.int16)


def silence(windows: int) -> np.ndarray:
    """Silence lasting ``windows`` VAD windows."""
    return np.zeros(windows * WINDOW, dtype=np.int16)


def to_frames(
    pcm: np.ndarray, sample_rate: int = SAMPLE_RATE, frame_ms: int = 20
) -> list[rtc.AudioFrame]:
    """Split mono s16 PCM into audio frames."""
    size = sample_rate * frame_ms // 1000
    return [
        rtc.AudioFrame(
            pcm[i : i + size].tobytes(), sample_rate, 1, len(pcm[i : i + size])
        )
        for i in range(0, len(pcm), size)
    ]


def read_wav(data: bytes) -> tuple[int, int, int, float]:
    """Return (channels, sample width, sample rate, duration) of a WAV body."""
    with wave.open(io.BytesIO(data)) as wav:
        return (
            wav.getnchannels(),
            wav.getsampwidth(),
            wav.getframerate(),
            wav.getnframes() / wav.getframerate(),
        )


class FakeVAD(vad.VAD):
    """Energy VAD emitting Silero-like events on 32 ms windows.

    It processes one window at a time and yields to the event loop in between,
    so ``clock()`` (audio time of the processed samples) is what a real-time
    stream would read with ``time.time()``.
    """

    def __init__(self, *, min_speech_s=0.05, min_silence_s=0.55, t0=1_792_404_000.0):
        """Init the fake VAD."""
        super().__init__(capabilities=vad.VADCapabilities(update_interval=WINDOW_S))
        self.min_speech_s = min_speech_s
        self.min_silence_s = min_silence_s
        self.t0 = t0
        self.processed_samples = 0

    def clock(self) -> float:
        """Wall clock matching the processed audio."""
        return self.t0 + self.processed_samples / SAMPLE_RATE

    def stream(self) -> "FakeVADStream":
        """Open a VAD stream."""
        return FakeVADStream(self)


class FakeVADStream(vad.VADStream):
    """Stream of FakeVAD."""

    async def _main_task(self) -> None:
        fake: FakeVAD = self._vad
        pending = np.zeros(0, dtype=np.int16)
        speaking = False
        speech = silence_d = speech_thr = silence_thr = 0.0
        async for item in self._input_ch:
            if isinstance(item, self._FlushSentinel):
                pending = np.zeros(0, dtype=np.int16)
                speaking = False
                speech = silence_d = speech_thr = silence_thr = 0.0
                continue
            pending = np.concatenate(
                [pending, np.frombuffer(item.data, dtype=np.int16)]
            )
            while len(pending) >= WINDOW:
                window, pending = pending[:WINDOW], pending[WINDOW:]
                fake.processed_samples += WINDOW
                probability = 1.0 if np.abs(window).mean() > 500 else 0.0
                if speaking:
                    speech += WINDOW_S
                else:
                    silence_d += WINDOW_S
                self._event_ch.send_nowait(
                    vad.VADEvent(
                        type=vad.VADEventType.INFERENCE_DONE,
                        samples_index=fake.processed_samples,
                        timestamp=fake.processed_samples / SAMPLE_RATE,
                        speech_duration=speech,
                        silence_duration=silence_d,
                        frames=[
                            rtc.AudioFrame(window.tobytes(), SAMPLE_RATE, 1, WINDOW)
                        ],
                        probability=probability,
                        speaking=speaking,
                    )
                )
                if probability >= 0.5:
                    speech_thr += WINDOW_S
                    silence_thr = 0.0
                    if not speaking and speech_thr >= fake.min_speech_s:
                        speaking = True
                        silence_d = 0.0
                        speech = speech_thr
                        self._event_ch.send_nowait(
                            vad.VADEvent(
                                type=vad.VADEventType.START_OF_SPEECH,
                                samples_index=fake.processed_samples,
                                timestamp=fake.processed_samples / SAMPLE_RATE,
                                speech_duration=speech,
                                silence_duration=0.0,
                                speaking=True,
                            )
                        )
                else:
                    silence_thr += WINDOW_S
                    speech_thr = 0.0
                    if speaking and silence_thr >= fake.min_silence_s:
                        speaking = False
                        silence_d = silence_thr
                        self._event_ch.send_nowait(
                            vad.VADEvent(
                                type=vad.VADEventType.END_OF_SPEECH,
                                samples_index=fake.processed_samples,
                                timestamp=fake.processed_samples / SAMPLE_RATE,
                                speech_duration=max(0.0, speech - silence_thr),
                                silence_duration=silence_thr,
                                speaking=False,
                            )
                        )
                        speech = 0.0
                # let the consumers handle the events of this window
                await asyncio.sleep(0.001)


@pytest.fixture
def fake_vad():
    """A fresh fake VAD."""
    return FakeVAD()


class FakeS3:
    """In-memory stand-in for the boto3 S3 client (the calls we use)."""

    def __init__(self, page_size: int = 2):
        """Init an empty store; listings are paginated by ``page_size``."""
        self.objects: dict[tuple[str, str], tuple[bytes, str | None]] = {}
        self.page_size = page_size
        self.fail_puts = 0  # number of next put_object calls that fail
        self.fail_put_keys: set[str] = set()  # keys whose writes always fail
        self.puts: list[str] = []

    def put_object(self, *, Bucket, Key, Body, ContentType=None):
        """Store an object."""
        if self.fail_puts > 0 or any(Key.endswith(k) for k in self.fail_put_keys):
            self.fail_puts = max(0, self.fail_puts - 1)
            raise ClientError({"Error": {"Code": "InternalError"}}, "PutObject")
        self.puts.append(Key)
        self.objects[(Bucket, Key)] = (bytes(Body), ContentType)
        return {}

    def get_object(self, *, Bucket, Key):
        """Read an object."""
        if (Bucket, Key) not in self.objects:
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")
        return {"Body": io.BytesIO(self.objects[(Bucket, Key)][0])}

    def list_objects_v2(self, *, Bucket, Prefix, ContinuationToken=None):
        """List keys by prefix, paginated."""
        keys = sorted(
            k for b, k in self.objects if b == Bucket and k.startswith(Prefix)
        )
        start = int(ContinuationToken or 0)
        page = keys[start : start + self.page_size]
        response = {"Contents": [{"Key": k} for k in page], "KeyCount": len(page)}
        if start + self.page_size < len(keys):
            response["IsTruncated"] = True
            response["NextContinuationToken"] = str(start + self.page_size)
        else:
            response["IsTruncated"] = False
        return response

    def keys(self, prefix: str = "") -> list[str]:
        """Sorted keys of all the objects under a prefix."""
        return sorted(k for _, k in self.objects if k.startswith(prefix))

    def json(self, key: str, bucket: str = "bucket"):
        """Decode a JSON object."""
        return json.loads(self.objects[(bucket, key)][0])

    def text(self, key: str, bucket: str = "bucket") -> str:
        """Decode a text object."""
        return self.objects[(bucket, key)][0].decode()


@pytest.fixture
def fake_s3():
    """A fresh fake S3 client."""
    return FakeS3()


class RecordingHook:
    """httpx MockTransport handler answering the agent hook."""

    def __init__(self, statuses: list[int] | None = None):
        """Answer with ``statuses`` in order, then 200."""
        self.statuses = list(statuses or [])
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Record the request and answer."""
        self.requests.append(request)
        status = self.statuses.pop(0) if self.statuses else 200
        return httpx.Response(status, json={"status": "ok"})

    @property
    def payloads(self) -> list[dict]:
        """Decoded request bodies."""
        return [json.loads(r.content) for r in self.requests]
