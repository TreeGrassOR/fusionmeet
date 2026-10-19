"""Shared helpers: settings, WAV builders, a fake engine and a running app client.

Nothing here imports torch: the model is replaced by ``FakeEngine``.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import struct
import threading
import time
import wave
from collections.abc import AsyncIterator, Callable, Sequence

import httpx
import numpy as np
import pytest

from asr_service.config import Settings
from asr_service.engine import EngineInfo
from asr_service.main import create_app

API_KEY = "test-secret-key"
AUTH = {"Authorization": f"Bearer {API_KEY}"}
WAV = {**AUTH, "Content-Type": "audio/wav"}
PCM = {**AUTH, "Content-Type": "audio/pcm"}


def make_settings(**overrides) -> Settings:
    """Return CPU settings with a short batching window, overridable per test."""
    values = {"api_key": API_KEY, "device": "cpu", "batch_wait_ms": 5.0}
    values.update(overrides)
    return Settings(**values)


def tone(seconds: float, rate: int = 16_000, freq: float = 440.0) -> np.ndarray:
    """Return a float32 sine wave with amplitude 0.5."""
    t = np.arange(int(seconds * rate)) / rate
    return (0.5 * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def pcm16(samples: np.ndarray) -> bytes:
    """Encode float samples as little-endian int16."""
    return (np.clip(samples, -1, 1) * 32767).astype("<i2").tobytes()


def wav_pcm16(samples: np.ndarray, rate: int = 16_000, channels: int = 1) -> bytes:
    """Build a 16-bit PCM WAV file with the standard library."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(channels)
        writer.setsampwidth(2)
        writer.setframerate(rate)
        writer.writeframes(pcm16(np.repeat(samples, channels)))
    return buffer.getvalue()


def wav_custom(  # noqa: PLR0913
    data: bytes,
    *,
    format_tag: int = 1,
    channels: int = 1,
    rate: int = 16_000,
    bits: int = 16,
    extensible_subformat: int | None = None,
    extra_chunks: bytes = b"",
) -> bytes:
    """Build a WAV file by hand (float, extensible, 24-bit, odd chunks...)."""
    block_align = channels * bits // 8
    fmt = struct.pack(
        "<HHIIHH",
        0xFFFE if extensible_subformat is not None else format_tag,
        channels,
        rate,
        rate * block_align,
        block_align,
        bits,
    )
    if extensible_subformat is not None:
        guid_tail = b"\x00\x00\x00\x00\x10\x00\x80\x00\x00\xaa\x00\x38\x9b\x71"
        fmt += struct.pack("<HHI", 22, bits, 0) + struct.pack(
            "<H", extensible_subformat
        )
        fmt += guid_tail
    chunks = b"fmt " + struct.pack("<I", len(fmt)) + fmt + extra_chunks
    chunks += b"data" + struct.pack("<I", len(data)) + data
    return b"RIFF" + struct.pack("<I", 4 + len(chunks)) + b"WAVE" + chunks


def wav_float32(samples: np.ndarray, rate: int = 16_000, channels: int = 1) -> bytes:
    """Build a 32-bit IEEE float WAV file."""
    data = np.repeat(samples, channels).astype("<f4").tobytes()
    return wav_custom(data, format_tag=3, channels=channels, rate=rate, bits=32)


class FakeEngine:
    """Stand-in for the GigaAM engine; the text of a clip is ``"clip <samples>"``."""

    def __init__(
        self,
        *,
        delay: float = 0.0,
        gate: threading.Event | None = None,
        error: Exception | None = None,
        variant: str = "large_ctc",
        revision: str = "rev-primary",
    ):
        """Optionally sleep, block on ``gate`` or raise ``error`` in each call."""
        self.info = EngineInfo(
            model_id="ai-sage/GigaAM-Multilingual",
            variant=variant,
            revision=revision,
            device="cpu",
            fp16_encoder=False,
        )
        self.calls: list[list[int]] = []
        self.received: list[np.ndarray] = []
        self.entered = threading.Event()
        self._delay = delay
        self._gate = gate
        self._error = error

    def transcribe_batch(self, wavs: Sequence[np.ndarray]) -> list[str]:
        """Record the batch and return one canned text per clip."""
        self.calls.append([wav.shape[0] for wav in wavs])
        self.received.extend(wavs)
        self.entered.set()
        if self._gate is not None:
            self._gate.wait(5)
        if self._delay:
            time.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return [f"clip {wav.shape[0]}" for wav in wavs]


def _never_fatal(code: int) -> None:
    raise AssertionError(f"unexpected fatal exit with status {code}")


@contextlib.asynccontextmanager
async def running_app(
    settings: Settings,
    engine: FakeEngine | Callable[[Settings], object],
    on_fatal: Callable[[int], None] = _never_fatal,
) -> AsyncIterator[tuple[object, httpx.AsyncClient]]:
    """Run the app lifespan and yield ``(app, client)``."""
    loader = engine if callable(engine) and not isinstance(engine, FakeEngine) else None
    app = create_app(
        settings,
        engine_loader=loader or (lambda _settings: engine),
        on_fatal=on_fatal,
    )
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://asr"
        ) as client:
            yield app, client


async def wait_until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
    """Poll ``predicate`` on the event loop until it is true."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not met in time")
        await asyncio.sleep(0.005)


async def wait_ready(client: httpx.AsyncClient) -> None:
    """Wait until ``/health`` reports the model as loaded."""
    deadline = time.monotonic() + 5
    while (await client.get("/health")).status_code != 200:
        if time.monotonic() > deadline:
            raise AssertionError("the service did not become ready")
        await asyncio.sleep(0.005)


@pytest.fixture
def engine() -> FakeEngine:
    """A fake engine that answers immediately."""
    return FakeEngine()


@pytest.fixture
async def client(engine: FakeEngine) -> AsyncIterator[httpx.AsyncClient]:
    """A client of a ready app backed by ``engine``."""
    async with running_app(make_settings(), engine) as (_app, http_client):
        await wait_ready(http_client)
        yield http_client
