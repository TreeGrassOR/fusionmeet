"""Tests of the torch GPU backend with a fake torch, and of real server processes.

Covered: CUDA probing, the memory limit, the GigaAM-v3 cross-load, fp16 handling and
out-of-memory errors while loading.
"""

import logging
import os
import socket
import subprocess
import sys
import time
import types
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from asr_service import engine as engine_module
from asr_service.engine import (
    GpuLoadError,
    GpuOutOfMemoryError,
    build_gigaam_engine,
    is_out_of_memory,
    limit_gpu_memory,
    load_gigaam_model,
    probe_gpu,
)
from asr_service.placement import GIB
from asr_service.registry import (
    GIGAAM_MULTILINGUAL_REPO,
    GIGAAM_MULTILINGUAL_REVISION,
    RECOGNIZERS,
)

from .conftest import fake_gpu, make_settings

ASR_DIR = Path(__file__).resolve().parents[1]
V3 = RECOGNIZERS["gigaam-v3"].gpu
MULTILINGUAL = RECOGNIZERS["gigaam-multilingual"].gpu


class FakeOutOfMemoryError(RuntimeError):
    """Plays torch.cuda.OutOfMemoryError."""


class FakeCuda:
    """The parts of torch.cuda used at startup."""

    OutOfMemoryError = FakeOutOfMemoryError

    def __init__(self, free_gb: float, total_gb: float = 24.0, available: bool = True):
        """Report ``free_gb`` free out of ``total_gb``."""
        self.free = int(free_gb * GIB)
        self.total = int(total_gb * GIB)
        self.available = available
        self.current = 0
        self.info_devices: list[int] = []
        self.fractions: list[tuple[float, int]] = []
        self.empty_cache_calls = 0

    def is_available(self) -> bool:
        """Pretend a GPU is (or is not) visible."""
        return self.available

    def current_device(self) -> int:
        """Return the index of the default GPU."""
        return self.current

    def mem_get_info(self, device: int) -> tuple[int, int]:
        """Return (free, total) bytes."""
        self.info_devices.append(device)
        return self.free, self.total

    def set_per_process_memory_fraction(self, fraction: float, device: int) -> None:
        """Record the limit; like torch, refuse a device without an index."""
        if not isinstance(device, int):
            raise ValueError(f"Expected a device index, got {device!r}")
        self.fractions.append((fraction, device))

    def empty_cache(self) -> None:
        """Count cache releases."""
        self.empty_cache_calls += 1


def fake_torch(cuda: FakeCuda):
    """A torch stand-in exposing only ``torch.cuda``."""
    return SimpleNamespace(cuda=cuda)


# --------------------------------------------------------------------- probing


@pytest.mark.parametrize(
    ("device", "current", "index"), [("cuda", 2, 2), ("cuda:1", 0, 1)]
)
def test_probe_uses_a_device_index(device, current, index):
    """ASR_DEVICE=cuda means the current GPU; cuda:<n> means GPU n."""
    cuda = FakeCuda(free_gb=10, total_gb=24)
    cuda.current = current
    gpu, reason = probe_gpu(fake_torch(cuda), make_settings(device=device))
    assert reason is None
    assert (gpu.index, gpu.free_bytes, gpu.total_bytes) == (index, 10 * GIB, 24 * GIB)
    assert cuda.info_devices == [index]


def test_probe_without_cuda_gives_a_reason():
    """No visible GPU is reported, not raised."""
    gpu, reason = probe_gpu(
        fake_torch(FakeCuda(10, available=False)), make_settings(device="cuda")
    )
    assert gpu is None
    assert "CUDA is not available" in reason


def test_memory_fraction_is_budget_over_total():
    """The allocator is limited to budget / total on the probed GPU."""
    cuda = FakeCuda(free_gb=10, total_gb=24)
    limit_gpu_memory(fake_torch(cuda), fake_gpu(10, 24, index=1), 4 * GIB)
    assert cuda.fractions == [(pytest.approx(4 / 24), 1)]


def test_is_out_of_memory():
    """OOM detection covers the dedicated type and the message forms."""
    torch = fake_torch(FakeCuda(1))
    assert is_out_of_memory(torch, FakeOutOfMemoryError("x"))
    assert is_out_of_memory(torch, RuntimeError("CUDA error: out of memory"))
    assert is_out_of_memory(
        torch, RuntimeError("CUBLAS_STATUS_ALLOC_FAILED when calling")
    )
    assert not is_out_of_memory(torch, RuntimeError("size mismatch"))
    assert not is_out_of_memory(torch, ValueError("out of memory"))


# ------------------------------------------------------------------ the loader


class FakeConfig:
    """Plays GigaAMConfig."""

    loaded: list = []

    @classmethod
    def from_pretrained(cls, repo, revision=None):
        """Record the call and return a fresh config."""
        cls.loaded.append((repo, revision))
        return cls()


class FakeGigaAMModel:
    """Plays modeling_gigaam.GigaAMModel of GigaAM-Multilingual."""

    config_class = FakeConfig
    calls: list = []

    @classmethod
    def from_pretrained(cls, repo, revision=None, config=None, **kwargs):
        """Record what the weights were loaded with."""
        cls.calls.append((repo, revision, config, kwargs))
        return "model"


@pytest.fixture
def fake_transformers(monkeypatch):
    """Fake ``transformers`` modules recording the loader's calls."""
    calls = {"dynamic": [], "auto": []}

    def get_class_from_dynamic_module(name, repo, revision=None):
        calls["dynamic"].append((name, repo, revision))
        return FakeGigaAMModel

    class AutoModel:
        @staticmethod
        def from_pretrained(repo, revision=None, trust_remote_code=False):
            calls["auto"].append((repo, revision, trust_remote_code))
            return "auto-model"

    package = types.ModuleType("transformers")
    package.AutoModel = AutoModel
    dynamic = types.ModuleType("transformers.dynamic_module_utils")
    dynamic.get_class_from_dynamic_module = get_class_from_dynamic_module
    package.dynamic_module_utils = dynamic
    monkeypatch.setitem(sys.modules, "transformers", package)
    monkeypatch.setitem(sys.modules, "transformers.dynamic_module_utils", dynamic)
    monkeypatch.delenv("DISABLE_SAFETENSORS_CONVERSION", raising=False)
    FakeConfig.loaded = []
    FakeGigaAMModel.calls = []
    return calls


def test_v3_weights_are_cross_loaded_into_the_multilingual_class(fake_transformers):
    """GigaAM-v3 uses the GigaAM-Multilingual class and its pinned v3 revision.

    The config gets ``_commit_hash`` = the v3 revision, so that tokenizer.model is
    looked up at that revision (not on "main", and offline without an OSError).
    """
    assert load_gigaam_model(V3) == "model"
    assert fake_transformers["dynamic"] == [
        (
            "modeling_gigaam.GigaAMModel",
            "ai-sage/GigaAM-Multilingual",
            "3905cd51c3ed4e88c8edf33f3302969ba480a327",
        )
    ]
    v3_revision = "7655ad717f8122257385bb4b2f373db3697e8680"
    assert FakeConfig.loaded == [("ai-sage/GigaAM-v3", v3_revision)]
    [(repo, revision, config, kwargs)] = FakeGigaAMModel.calls
    assert (repo, revision, kwargs) == ("ai-sage/GigaAM-v3", v3_revision, {})
    assert config._commit_hash == v3_revision
    assert fake_transformers["auto"] == []
    # no unpinned safetensors conversion is fetched in the background
    assert os.environ["DISABLE_SAFETENSORS_CONVERSION"] == "true"


def test_multilingual_loads_through_automodel(fake_transformers):
    """gigaam-multilingual keeps today's AutoModel path with its own remote code."""
    assert load_gigaam_model(MULTILINGUAL) == "auto-model"
    assert fake_transformers["auto"] == [
        (GIGAAM_MULTILINGUAL_REPO, GIGAAM_MULTILINGUAL_REVISION, True)
    ]
    assert fake_transformers["dynamic"] == []


class FakeEncoder:
    """An encoder that remembers its dtype."""

    def __init__(self):
        """Start in float32."""
        self.dtype = "float32"

    def half(self):
        """Cast to fp16."""
        self.dtype = "float16"
        return self

    def float(self):
        """Cast back to fp32."""
        self.dtype = "float32"
        return self


class FakeModel:
    """Plays GigaAMModel: ``model.model.encoder``, eval() and to()."""

    def __init__(self):
        """Nothing moved yet."""
        self.model = SimpleNamespace(encoder=FakeEncoder())
        self.device = None
        self.evaluated = False

    def eval(self):
        """Inference mode."""
        self.evaluated = True

    def to(self, device):
        """Record the device."""
        self.device = device


class FakeEngineClass:
    """Replaces GigaAMEngine: warm_up raises the queued errors in order."""

    errors: list = []
    warmups: list = []

    def __init__(self, torch, model, info):
        """Keep the model and its description."""
        self.model = model
        self.info = info

    def warm_up(self, settings):
        """Record the encoder dtype and raise the next queued error, if any."""
        FakeEngineClass.warmups.append(self.model.model.encoder.dtype)
        if FakeEngineClass.errors:
            raise FakeEngineClass.errors.pop(0)


@pytest.fixture
def fake_load(monkeypatch):
    """Patch the loader and GigaAMEngine; return the model that will be loaded."""
    model = FakeModel()
    monkeypatch.setattr(engine_module, "load_gigaam_model", lambda backend: model)
    monkeypatch.setattr(engine_module, "GigaAMEngine", FakeEngineClass)
    FakeEngineClass.errors = []
    FakeEngineClass.warmups = []
    return model


def test_cuda_load_halves_only_the_encoder(fake_load):
    """On CUDA the encoder is fp16, the model is moved and warmed up once."""
    torch = fake_torch(FakeCuda(10))
    engine = build_gigaam_engine(torch, make_settings(device="cuda"), V3, "cuda:0")
    assert fake_load.evaluated
    assert fake_load.device == "cuda:0"
    assert FakeEngineClass.warmups == ["float16"]
    assert engine.info.fp16_encoder is True
    assert engine.info.model_id == "ai-sage/GigaAM-v3"
    assert engine.info.variant == "v3_e2e_rnnt"
    assert engine.info.device == "cuda:0"


def test_fp16_warm_up_failure_retries_in_float32(fake_load):
    """A non-memory error with the fp16 encoder retries in float32."""
    FakeEngineClass.errors = [RuntimeError("expected scalar type Half")]
    torch = fake_torch(FakeCuda(10))
    engine = build_gigaam_engine(torch, make_settings(device="cuda"), V3, "cuda:0")
    assert FakeEngineClass.warmups == ["float16", "float32"]
    assert engine.info.fp16_encoder is False


def test_fp16_can_be_disabled(fake_load):
    """ASR_FP16_ENCODER=false keeps the encoder in float32."""
    torch = fake_torch(FakeCuda(10))
    settings = make_settings(device="cuda", fp16_encoder=False)
    engine = build_gigaam_engine(torch, settings, V3, "cuda:0")
    assert FakeEngineClass.warmups == ["float32"]
    assert engine.info.fp16_encoder is False


def test_cpu_load_stays_in_float32(fake_load):
    """On the CPU (a dev check of the GPU path) nothing is halved."""
    torch = fake_torch(FakeCuda(10))
    engine = build_gigaam_engine(torch, make_settings(device="cpu"), V3, "cpu")
    assert FakeEngineClass.warmups == ["float32"]
    assert engine.info.fp16_encoder is False


def test_out_of_memory_while_loading_frees_memory_and_raises(fake_load):
    """A CUDA OOM (here in the warm-up) becomes GpuOutOfMemoryError."""
    FakeEngineClass.errors = [FakeOutOfMemoryError("CUDA out of memory")]
    cuda = FakeCuda(10)
    settings = make_settings(device="cuda")
    with pytest.raises(GpuOutOfMemoryError, match="CUDA out of memory while loading"):
        build_gigaam_engine(fake_torch(cuda), settings, V3, "cuda:0")
    assert cuda.empty_cache_calls == 1


def test_other_loading_errors_free_memory_and_raise(monkeypatch, caplog):
    """Another failure is logged with its traceback and becomes GpuLoadError.

    It is not mistaken for an out-of-memory error.
    """

    def fail(backend):
        raise OSError("cannot reach huggingface.co")

    monkeypatch.setattr(engine_module, "load_gigaam_model", fail)
    cuda = FakeCuda(10)
    with pytest.raises(GpuLoadError, match="cannot reach huggingface.co") as raised:
        build_gigaam_engine(
            fake_torch(cuda), make_settings(device="cuda"), V3, "cuda:0"
        )
    assert not isinstance(raised.value, GpuOutOfMemoryError)
    # The traceback (and the half-loaded model it references) is not kept.
    assert raised.value.__context__ is None
    assert cuda.empty_cache_calls == 1
    [record] = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert record.name == "asr_service.engine"
    assert record.exc_info is not None


# ------------------------------------------------------- real server processes


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _get(url: str) -> tuple[int, bytes]:
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=2) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def test_uvicorn_process_without_gpu_keeps_serving(tmp_path):
    """A real server with ASR_DEVICE=cuda and no GPU does not exit any more.

    Every model goes to the CPU. Here the Hub is offline and the cache empty, so the
    model fails to load: /health stays 503 "failed" and the process keeps running.
    """
    port = _free_port()
    env = {
        **{k: v for k, v in os.environ.items() if not k.startswith("ASR_")},
        "ASR_API_KEY": "secret",
        "ASR_DEVICE": "cuda",
        "ASR_MODELS": "gigaam-v3",
        "ASR_MODEL_REVISION": "old-revision",
        # Hide any GPU and the network, so the test behaves the same everywhere.
        "CUDA_VISIBLE_DEVICES": "",
        "HF_HUB_OFFLINE": "1",
        "HF_HOME": str(tmp_path),
    }
    process = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            "-m",
            "uvicorn",
            "asr_service.main:create_app",
            "--factory",
            "--port",
            str(port),
        ],
        cwd=ASR_DIR,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        status, body = 0, b""
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline and b'"failed"' not in body:
            time.sleep(0.2)
            try:
                status, body = _get(f"http://127.0.0.1:{port}/health")
            except OSError:
                continue
        assert status == 503
        assert b'"status":"failed"' in body
        assert process.poll() is None
    finally:
        process.terminate()
        output, _ = process.communicate(timeout=30)
    assert "run(s) on the CPU instead" in output
    assert "Ignoring ASR_MODEL_REVISION" in output
    assert "Failed to load gigaam-v3" in output


def _run_factory(env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "uvicorn", "asr_service.main:create_app", "--factory"],
        cwd=ASR_DIR,
        env={
            **{k: v for k, v in os.environ.items() if not k.startswith("ASR_")},
            **env,
        },
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def test_uvicorn_process_refuses_to_start_without_api_key():
    """An empty ASR_API_KEY stops the process before it serves anything."""
    result = _run_factory({})
    assert result.returncode == 1
    assert "ASR_API_KEY is required" in result.stderr


def test_uvicorn_process_refuses_a_default_model_that_is_not_enabled():
    """A configuration error is fatal at startup with a clear message."""
    result = _run_factory({"ASR_API_KEY": "secret", "ASR_MODELS": "parakeet-v3"})
    assert result.returncode == 1
    assert "ASR_DEFAULT_MODEL=gigaam-v3 is not enabled in ASR_MODELS" in result.stderr
