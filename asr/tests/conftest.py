"""Shared helpers: settings, WAV builders, fake engines and a running app client.

Nothing here imports torch or onnxruntime: the models are replaced by ``FakeEngine``
through ``FakeBackends``.
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
from asr_service.engine import EngineInfo, GpuOutOfMemoryError
from asr_service.main import create_app
from asr_service.placement import GIB, GpuInfo

API_KEY = "test-secret-key"
AUTH = {"Authorization": f"Bearer {API_KEY}"}
WAV = {**AUTH, "Content-Type": "audio/wav"}
PCM = {**AUTH, "Content-Type": "audio/pcm"}


def make_settings(**overrides) -> Settings:
    """Return CPU settings with a short batching window, overridable per test."""
    values = {
        "api_key": API_KEY,
        "device": "cpu",
        "batch_wait_ms": 5.0,
        "cpu_threads": 2,
    }
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
    """Stand-in for a recogniser; the text of a clip is ``"<text> <samples>"``."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        delay: float = 0.0,
        gate: threading.Event | None = None,
        error: Exception | None = None,
        text: str = "clip",
        model_id: str = "fake/repo",
        variant: str = "fake-variant",
        revision: str = "rev-fake",
        device: str = "cpu",
    ):
        """Optionally sleep, block on ``gate`` or raise ``error`` in each call."""
        self.info = EngineInfo(
            model_id=model_id, variant=variant, revision=revision, device=device
        )
        self.calls: list[list[int]] = []
        self.received: list[np.ndarray] = []
        self.entered = threading.Event()
        self.text = text
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
        return [f"{self.text} {wav.shape[0]}" for wav in wavs]


class FakeBackends:
    """Places and "loads" FakeEngines, recording what the manager asked for.

    ``engines`` maps a recogniser key to its engine (a default one is made per key);
    ``failures`` maps ``"<key>"`` (CPU or punctuator) or ``"<key>@gpu"`` to the
    exception that loading raises; ``gates`` maps a key to an event its loading
    waits for.
    """

    def __init__(  # noqa: PLR0913
        self,
        engines: dict[str, FakeEngine] | None = None,
        *,
        gpu: GpuInfo | None = None,
        no_gpu_reason: str | None = "CUDA is not available (fake)",
        failures: dict[str, Exception] | None = None,
        gates: dict[str, threading.Event] | None = None,
        punctuate: Callable[[str], str] | None = None,
    ):
        """Configure the fake hardware and per-component outcomes."""
        self.engines = dict(engines or {})
        self.gpu = gpu
        self.no_gpu_reason = no_gpu_reason
        self.failures = failures or {}
        self.gates = gates or {}
        self.punctuate = punctuate or (lambda text: f"P({text})")
        self.loaded: list[tuple[str, str]] = []
        self.limits: list[tuple[GpuInfo, int]] = []
        self.probes = 0
        self.punctuated: list[str] = []

    def _engine(self, key: str, device: str) -> FakeEngine:
        engine = self.engines.get(key)
        if engine is None:
            engine = self.engines[key] = FakeEngine(
                text=key, model_id=f"repo/{key}", variant=f"{key}-{device}"
            )
        return engine

    def _start(self, name: str) -> None:
        gate = self.gates.get(name.split("@", 1)[0])
        if gate is not None:
            gate.wait(5)
        if name in self.failures:
            raise self.failures[name]

    def probe_gpu(self, settings):
        """Report the fake GPU (or its absence)."""
        self.probes += 1
        if self.gpu is None:
            return None, self.no_gpu_reason
        return self.gpu, None

    def limit_gpu_memory(self, gpu, budget_bytes):
        """Record the allocator limit."""
        self.limits.append((gpu, budget_bytes))

    def build_gpu(self, settings, backend, device):
        """Return the recogniser's engine as if loaded on the GPU."""
        key = _key_of(backend)
        self.loaded.append((key, device))
        self._start(f"{key}@gpu")
        return self._engine(key, "gpu")

    def build_cpu(self, settings, backend):
        """Return the recogniser's engine as if loaded on the CPU."""
        key = _key_of(backend)
        self.loaded.append((key, "cpu"))
        self._start(key)
        return self._engine(key, "cpu")

    def build_punctuator(self, settings, spec):
        """Return a fake punctuation function."""
        self.loaded.append((spec.key, "cpu"))
        self._start(spec.key)

        def punctuate(text: str) -> str:
            self.punctuated.append(text)
            return self.punctuate(text)

        return punctuate


def _key_of(backend) -> str:
    from asr_service.registry import RECOGNIZERS  # noqa: PLC0415

    for key, recognizer in RECOGNIZERS.items():
        if backend in (recognizer.cpu, recognizer.gpu):
            return key
    raise AssertionError(f"unknown backend {backend!r}")


def fake_gpu(free_gb: float = 10.0, total_gb: float = 24.0, index: int = 0) -> GpuInfo:
    """A GPU with ``free_gb`` free out of ``total_gb``."""
    return GpuInfo(index, int(free_gb * GIB), int(total_gb * GIB))


def oom(key: str) -> GpuOutOfMemoryError:
    """The error RealBackends.build_gpu raises when CUDA runs out of memory."""
    return GpuOutOfMemoryError(f"CUDA out of memory while loading {key}")


@contextlib.asynccontextmanager
async def running_app(
    settings: Settings, backends: FakeBackends | None = None
) -> AsyncIterator[tuple[object, httpx.AsyncClient]]:
    """Run the app lifespan and yield ``(app, client)``."""
    app = create_app(settings, backends=backends or FakeBackends())
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
    """Wait until ``/health`` reports the default model as loaded."""
    deadline = time.monotonic() + 5
    while (await client.get("/health")).status_code != 200:
        if time.monotonic() > deadline:
            raise AssertionError("the service did not become ready")
        await asyncio.sleep(0.005)


async def wait_loaded(app) -> None:
    """Wait until every model is ready or failed."""
    manager = app.state.asr.manager
    await wait_until(
        lambda: all(
            manager.status(model)[0] != "loading" for model in manager.models.values()
        )
    )


@pytest.fixture
def engine() -> FakeEngine:
    """The fake engine of the default model (gigaam-v3), answering at once."""
    return FakeEngine(model_id="ai-sage/GigaAM-v3", variant="v3", revision="rev-v3")


@pytest.fixture
def backends(engine: FakeEngine) -> FakeBackends:
    """Fake backends whose default recogniser is ``engine``."""
    return FakeBackends({"gigaam-v3": engine})


@pytest.fixture
async def client(backends: FakeBackends) -> AsyncIterator[httpx.AsyncClient]:
    """A client of an app whose models are all loaded (on the CPU)."""
    async with running_app(make_settings(), backends) as (app, http_client):
        await wait_ready(http_client)
        await wait_loaded(app)
        yield http_client
