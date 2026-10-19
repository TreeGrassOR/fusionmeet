"""Tests of the VRAM check, variant selection and fatal startup errors (fake torch)."""

import logging
import os
import socket
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from asr_service import main
from asr_service.engine import (
    GIB,
    FatalStartupError,
    is_out_of_memory,
    load_engine,
    plan_vram,
)

from .conftest import FakeEngine, make_settings, running_app, wait_until

ASR_DIR = Path(__file__).resolve().parents[1]


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


class Builder:
    """Fake per-variant loader that records what was attempted."""

    def __init__(self, failures: dict[str, Exception] | None = None):
        """``failures`` maps a variant name to the exception its loading raises."""
        self.failures = failures or {}
        self.attempts: list[str] = []
        self.plans = []

    def __call__(self, settings, spec, plan):
        """Build a FakeEngine for ``spec`` or raise its configured failure."""
        self.attempts.append(spec.variant)
        self.plans.append(plan)
        if spec.variant in self.failures:
            raise self.failures[spec.variant]
        return FakeEngine(variant=spec.variant, revision=spec.revision)


def cuda_settings(**overrides):
    """Settings for a CUDA device with the default VRAM figures."""
    return make_settings(device="cuda", **overrides)


def fake_torch(cuda: FakeCuda):
    """A torch stand-in exposing only ``torch.cuda``."""
    return SimpleNamespace(cuda=cuda)


def test_enough_vram_loads_the_primary(caplog):
    """With budget >= ASR_MODEL_VRAM_GB the primary (large_ctc) is loaded."""
    cuda = FakeCuda(free_gb=10)
    builder = Builder()
    with caplog.at_level(logging.INFO, logger="asr_service"):
        engine = load_engine(
            cuda_settings(), torch_module=fake_torch(cuda), build_engine=builder
        )
    assert engine.info.variant == "large_ctc"
    assert engine.info.revision == "3905cd51c3ed4e88c8edf33f3302969ba480a327"
    assert builder.attempts == ["large_ctc"]
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_memory_fraction_is_budget_over_total():
    """The allocator is limited to min(ASR_MAX_VRAM_GB, free) / total."""
    cuda = FakeCuda(free_gb=10, total_gb=24)
    builder = Builder()
    load_engine(
        cuda_settings(max_vram_gb=4),
        torch_module=fake_torch(cuda),
        build_engine=builder,
    )
    assert cuda.fractions == [(pytest.approx(4 / 24), 0)]
    assert builder.plans[0].budget_bytes == 4 * GIB

    cuda = FakeCuda(free_gb=3.5, total_gb=24)
    load_engine(
        cuda_settings(max_vram_gb=4),
        torch_module=fake_torch(cuda),
        build_engine=Builder(),
    )
    assert cuda.fractions == [(pytest.approx(3.5 / 24), 0)]


@pytest.mark.parametrize(
    ("device", "current", "index"), [("cuda", 2, 2), ("cuda:1", 0, 1)]
)
def test_vram_calls_use_a_device_index(device, current, index):
    """ASR_DEVICE=cuda means the current GPU; cuda:<n> means GPU n."""
    cuda = FakeCuda(free_gb=10, total_gb=24)
    cuda.current = current
    load_engine(
        make_settings(device=device),
        torch_module=fake_torch(cuda),
        build_engine=Builder(),
    )
    assert cuda.info_devices == [index]
    assert cuda.fractions == [(pytest.approx(4 / 24), index)]


def test_only_fallback_fits_loads_fallback_with_warning(caplog):
    """When only ctc fits, it is loaded and a WARNING explains why."""
    cuda = FakeCuda(free_gb=2.0)
    builder = Builder()
    with caplog.at_level(logging.INFO, logger="asr_service"):
        engine = load_engine(
            cuda_settings(), torch_module=fake_torch(cuda), build_engine=builder
        )
    assert engine.info.variant == "ctc"
    assert engine.info.revision == "2f8a57144e6ec3adfd32fe0484d9ea9913305bc8"
    assert builder.attempts == ["ctc"]
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "Not enough VRAM for large_ctc" in warnings[0]
    assert "Loading the fallback ctc" in warnings[0]
    assert cuda.fractions == [(pytest.approx(2.0 / 24), 0)]


def test_low_max_vram_setting_also_selects_the_fallback():
    """ASR_MAX_VRAM_GB below the primary requirement selects the fallback."""
    cuda = FakeCuda(free_gb=20)
    engine = load_engine(
        cuda_settings(max_vram_gb=2),
        torch_module=fake_torch(cuda),
        build_engine=Builder(),
    )
    assert engine.info.variant == "ctc"


def test_nothing_fits_raises_a_clear_error():
    """Without room for the fallback, startup fails with an actionable message."""
    cuda = FakeCuda(free_gb=1.0, total_gb=24)
    builder = Builder()
    with pytest.raises(FatalStartupError) as excinfo:
        load_engine(
            cuda_settings(), torch_module=fake_torch(cuda), build_engine=builder
        )
    message = str(excinfo.value)
    assert "Not enough GPU memory for the ASR model" in message
    assert "1.00 GiB free of 24.00 GiB" in message
    assert "large_ctc needs 3.00 GiB" in message
    assert "ctc needs 1.50 GiB" in message
    assert "free at least 0.50 GiB more" in message
    assert "--gpu-memory-utilization" in message
    assert builder.attempts == []
    assert cuda.fractions == []


def test_nothing_fits_because_of_max_vram_setting():
    """The message points at ASR_MAX_VRAM_GB when it is the limiting factor."""
    with pytest.raises(
        FatalStartupError, match="raise ASR_MAX_VRAM_GB to at least 1.5"
    ):
        plan_vram(
            cuda_settings(max_vram_gb=1), free_bytes=20 * GIB, total_bytes=24 * GIB
        )


def test_no_fallback_configured():
    """With ASR_FALLBACK_REVISION empty, only the primary is considered."""
    settings = cuda_settings(fallback_revision=None)
    assert [s.variant for s in plan_vram(settings, 10 * GIB, 24 * GIB).candidates] == [
        "large_ctc"
    ]
    with pytest.raises(FatalStartupError, match="large_ctc needs 3.00 GiB"):
        plan_vram(settings, 2 * GIB, 24 * GIB)


def test_oom_while_loading_primary_falls_back(caplog):
    """A CUDA OOM while loading large_ctc frees memory and loads ctc instead."""
    cuda = FakeCuda(free_gb=10)
    builder = Builder(
        {"large_ctc": FakeOutOfMemoryError("CUDA out of memory. Tried to allocate")}
    )
    with caplog.at_level(logging.WARNING, logger="asr_service"):
        engine = load_engine(
            cuda_settings(), torch_module=fake_torch(cuda), build_engine=builder
        )
    assert engine.info.variant == "ctc"
    assert builder.attempts == ["large_ctc", "ctc"]
    assert cuda.empty_cache_calls == 1
    assert any("falling back to ctc" in r.getMessage() for r in caplog.records)


def test_oom_on_every_variant_is_fatal():
    """If the fallback also runs out of memory, startup fails."""
    cuda = FakeCuda(free_gb=10)
    oom = RuntimeError("CUDA error: out of memory")
    builder = Builder({"large_ctc": oom, "ctc": oom})
    with pytest.raises(FatalStartupError, match="Out of memory while loading ctc"):
        load_engine(
            cuda_settings(), torch_module=fake_torch(cuda), build_engine=builder
        )
    assert builder.attempts == ["large_ctc", "ctc"]


def test_other_loading_errors_are_fatal_without_fallback():
    """A non-memory error (e.g. download failure) is reported, not masked by ctc."""
    cuda = FakeCuda(free_gb=10)
    builder = Builder({"large_ctc": OSError("cannot reach huggingface.co")})
    with pytest.raises(FatalStartupError, match="cannot reach huggingface.co"):
        load_engine(
            cuda_settings(), torch_module=fake_torch(cuda), build_engine=builder
        )
    assert builder.attempts == ["large_ctc"]


def test_cuda_unavailable_is_fatal():
    """ASR_DEVICE=cuda without a visible GPU fails with a hint."""
    cuda = FakeCuda(free_gb=10, available=False)
    with pytest.raises(FatalStartupError, match="CUDA is not available"):
        load_engine(
            cuda_settings(), torch_module=fake_torch(cuda), build_engine=Builder()
        )


def test_cpu_device_skips_the_vram_logic():
    """On CPU (tests only) the primary is loaded without touching torch.cuda."""
    builder = Builder()
    engine = load_engine(
        make_settings(device="cpu"), torch_module=object(), build_engine=builder
    )
    assert engine.info.variant == "large_ctc"
    assert builder.plans == [None]


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


async def test_app_exits_with_status_1_when_nothing_fits(caplog):
    """The app logs the reason at CRITICAL and requests exit status 1."""
    cuda = FakeCuda(free_gb=0.5)
    exits: list[int] = []

    def loader(settings):
        return load_engine(
            settings, torch_module=fake_torch(cuda), build_engine=Builder()
        )

    with caplog.at_level(logging.INFO, logger="asr_service"):
        async with running_app(cuda_settings(), loader, on_fatal=exits.append) as (
            _app,
            client,
        ):
            await wait_until(lambda: exits == [1])
            assert (await client.get("/health")).status_code == 503
    critical = [r.getMessage() for r in caplog.records if r.levelno == logging.CRITICAL]
    assert len(critical) == 1
    assert "ASR service cannot start: Not enough GPU memory" in critical[0]


def test_exit_process_terminates_with_the_status(monkeypatch):
    """The default fatal handler exits the process immediately."""
    codes: list[int] = []
    monkeypatch.setattr(os, "_exit", codes.append)
    main.exit_process(1)
    assert codes == [1]


def test_uvicorn_process_exits_with_a_clear_error():
    """A real server process with no usable GPU exits with status 1 and says why."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {
        **os.environ,
        "ASR_API_KEY": "secret",
        "ASR_DEVICE": "cuda",
        # Hide any GPU, so the test behaves the same with or without torch installed.
        "CUDA_VISIBLE_DEVICES": "",
    }
    result = subprocess.run(  # noqa: S603
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
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 1, result.stderr
    assert "ASR service cannot start" in result.stderr


def test_uvicorn_process_refuses_to_start_without_api_key():
    """An empty ASR_API_KEY stops the process before it serves anything."""
    env = {
        key: value for key, value in os.environ.items() if not key.startswith("ASR_")
    }
    result = subprocess.run(
        [sys.executable, "-m", "uvicorn", "asr_service.main:create_app", "--factory"],
        cwd=ASR_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 1
    assert "ASR_API_KEY is required" in result.stderr
