"""GigaAM model loading (VRAM budget, fallback variant) and batched inference.

``torch`` and ``transformers`` are imported lazily, inside the functions that need
them, so the HTTP layer and the unit tests run without them.
"""

from __future__ import annotations

import gc
import importlib
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

import numpy as np

from .audio import SAMPLE_RATE
from .config import Settings

logger = logging.getLogger(__name__)

GIB = 1024**3
# Clips shorter than this are answered with "" without running the model: torch.stft
# fails on fewer than 320 samples, and 0.1 s cannot hold a word anyway.
MIN_SAMPLES = SAMPLE_RATE // 10


class FatalStartupError(RuntimeError):
    """The model cannot be served with this configuration or hardware."""


@dataclass(frozen=True)
class ModelSpec:
    """One loadable model variant."""

    variant: str
    revision: str
    vram_gb: float


@dataclass(frozen=True)
class VramPlan:
    """Outcome of the VRAM check done before loading on CUDA."""

    candidates: tuple[ModelSpec, ...]
    free_bytes: int
    total_bytes: int
    budget_bytes: int

    @property
    def memory_fraction(self) -> float:
        """Fraction passed to ``torch.cuda.set_per_process_memory_fraction``."""
        return self.budget_bytes / self.total_bytes


@dataclass(frozen=True)
class EngineInfo:
    """Description of the loaded model, reported by ``/health`` and responses."""

    model_id: str
    variant: str
    revision: str
    device: str
    fp16_encoder: bool
    vram_limit_gb: float | None = None
    vram_free_gb_at_start: float | None = None


class Engine(Protocol):
    """What the HTTP layer needs from a loaded model."""

    info: EngineInfo

    def transcribe_batch(self, wavs: Sequence[np.ndarray]) -> list[str]:
        """Transcribe mono float32 16 kHz clips; return one text per clip."""
        ...


BuildEngine = Callable[[Settings, ModelSpec, VramPlan | None], Engine]


def model_specs(settings: Settings) -> tuple[ModelSpec, ModelSpec | None]:
    """Return the primary and the optional fallback model variants."""
    primary = ModelSpec(
        settings.model_variant, settings.model_revision, settings.model_vram_gb
    )
    fallback = None
    if settings.fallback_revision:
        fallback = ModelSpec(
            settings.fallback_variant,
            settings.fallback_revision,
            settings.fallback_vram_gb,
        )
    return primary, fallback


def plan_vram(settings: Settings, free_bytes: int, total_bytes: int) -> VramPlan:
    """Choose which variants to try given the free GPU memory.

    The budget is ``min(ASR_MAX_VRAM_GB, free memory)``. The primary variant is used
    when its requirement fits in the budget (the fallback is then kept as a plan B for
    a CUDA out-of-memory error during loading); otherwise the fallback is used.

    Raises:
        FatalStartupError: when not even the fallback fits.
    """
    primary, fallback = model_specs(settings)
    budget = min(int(settings.max_vram_gb * GIB), int(free_bytes))
    budget_gb = budget / GIB
    fallback_fits = fallback is not None and budget_gb >= fallback.vram_gb

    if budget_gb >= primary.vram_gb:
        candidates = (primary, fallback) if fallback_fits else (primary,)
        logger.info(
            "VRAM budget %.2f GiB (free %.2f of %.2f GiB, ASR_MAX_VRAM_GB=%g): "
            "loading %s, which needs %.2f GiB",
            budget_gb,
            free_bytes / GIB,
            total_bytes / GIB,
            settings.max_vram_gb,
            primary.variant,
            primary.vram_gb,
        )
    elif fallback_fits:
        candidates = (fallback,)
        logger.warning(
            "Not enough VRAM for %s: it needs %.2f GiB but the budget is only %.2f GiB "
            "(free %.2f of %.2f GiB, ASR_MAX_VRAM_GB=%g). Loading the fallback %s "
            "(revision %s, needs %.2f GiB) instead; recognition quality will be lower",
            primary.variant,
            primary.vram_gb,
            budget_gb,
            free_bytes / GIB,
            total_bytes / GIB,
            settings.max_vram_gb,
            fallback.variant,
            fallback.revision,
            fallback.vram_gb,
        )
    else:
        raise FatalStartupError(
            _not_enough_vram_message(
                settings, primary, fallback, free_bytes, total_bytes
            )
        )
    return VramPlan(candidates, int(free_bytes), int(total_bytes), budget)


def _not_enough_vram_message(
    settings: Settings,
    primary: ModelSpec,
    fallback: ModelSpec | None,
    free_bytes: int,
    total_bytes: int,
) -> str:
    smallest = fallback if fallback is not None else primary
    needs = f"{primary.variant} needs {primary.vram_gb:.2f} GiB"
    if fallback is not None:
        needs += f", the fallback {fallback.variant} needs {fallback.vram_gb:.2f} GiB"
    free_gb = free_bytes / GIB
    hints = []
    if free_gb < smallest.vram_gb:
        hints.append(
            f"free at least {smallest.vram_gb - free_gb:.2f} GiB more on the GPU, "
            "for example by lowering --gpu-memory-utilization of the vLLM server "
            "that shares it, then restart this service"
        )
    if settings.max_vram_gb < smallest.vram_gb:
        hints.append(f"raise ASR_MAX_VRAM_GB to at least {smallest.vram_gb:g}")
    return (
        f"Not enough GPU memory for the ASR model: {free_gb:.2f} GiB free of "
        f"{total_bytes / GIB:.2f} GiB on {settings.device}, ASR_MAX_VRAM_GB="
        f"{settings.max_vram_gb:g}; {needs}. To fix it: " + "; ".join(hints) + "."
    )


def is_out_of_memory(torch: Any, exc: BaseException) -> bool:
    """Tell whether ``exc`` is a CUDA out-of-memory error."""
    oom_type = getattr(getattr(torch, "cuda", None), "OutOfMemoryError", None)
    if isinstance(oom_type, type) and isinstance(exc, oom_type):
        return True
    message = str(exc).lower()
    return isinstance(exc, RuntimeError) and (
        "out of memory" in message or "cublas_status_alloc_failed" in message
    )


def release_cuda_memory(torch: Any) -> None:
    """Free unreferenced tensors and return cached CUDA blocks to the driver."""
    gc.collect()
    if torch.cuda.is_available():
        torch.cuda.empty_cache()


def load_engine(
    settings: Settings,
    *,
    torch_module: Any = None,
    build_engine: BuildEngine | None = None,
) -> Engine:
    """Check the GPU memory, limit this process to its budget and load the model.

    Args:
        settings: the service settings.
        torch_module: the ``torch`` module (injected by tests).
        build_engine: loads one variant (injected by tests).

    Raises:
        FatalStartupError: with a human-readable explanation, when no variant can be
            loaded.
    """
    torch = (
        torch_module if torch_module is not None else importlib.import_module("torch")
    )
    if build_engine is None:

        def build_engine(s: Settings, spec: ModelSpec, plan: VramPlan | None) -> Engine:
            return build_gigaam_engine(torch, s, spec, plan)

    plan: VramPlan | None = None
    if settings.device_type == "cuda":
        if not torch.cuda.is_available():
            raise FatalStartupError(
                f"ASR_DEVICE={settings.device} but CUDA is not available in this "
                "container: check the NVIDIA driver, the NVIDIA Container Toolkit and "
                "the GPU reservation of the asr service"
            )
        # set_per_process_memory_fraction() raises ValueError for a device without
        # an index ("cuda"), so resolve it to the current device first.
        index = settings.device_index
        if index is None:
            index = torch.cuda.current_device()
        free_bytes, total_bytes = torch.cuda.mem_get_info(index)
        plan = plan_vram(settings, free_bytes, total_bytes)
        torch.cuda.set_per_process_memory_fraction(plan.memory_fraction, index)
        logger.info(
            "Limited the CUDA caching allocator of this process to %.2f GiB "
            "(fraction %.4f of %.2f GiB)",
            plan.budget_bytes / GIB,
            plan.memory_fraction,
            total_bytes / GIB,
        )
        candidates = plan.candidates
    else:
        logger.warning(
            "ASR_DEVICE=%s: running on the CPU, meant for tests only", settings.device
        )
        candidates = (model_specs(settings)[0],)

    for index, spec in enumerate(candidates):
        try:
            return build_engine(settings, spec, plan)
        except FatalStartupError:
            raise
        except Exception as exc:
            if not is_out_of_memory(torch, exc):
                raise FatalStartupError(
                    f"Failed to load {settings.model_id} {spec.variant} "
                    f"(revision {spec.revision}): {type(exc).__name__}: {exc}"
                ) from exc
            error = f"{type(exc).__name__}: {exc}"
        # Outside the except block, so that the traceback and the half-loaded model it
        # references are released before the cache is emptied.
        release_cuda_memory(torch)
        if index + 1 == len(candidates):
            budget = (
                ""
                if plan is None
                else f" with a budget of {plan.budget_bytes / GIB:.2f} GiB"
            )
            raise FatalStartupError(
                f"Out of memory while loading {spec.variant} (revision {spec.revision})"
                f"{budget}: {error}. Free more GPU memory (e.g. lower "
                "--gpu-memory-utilization of vLLM) or lower ASR_MAX_BATCH_SIZE / "
                "ASR_MAX_BATCH_AUDIO_SECONDS"
            )
        logger.warning(
            "CUDA out of memory while loading %s (%s); "
            "falling back to %s (revision %s)",
            spec.variant,
            error,
            candidates[index + 1].variant,
            candidates[index + 1].revision,
        )
    raise AssertionError("unreachable")  # pragma: no cover


def build_gigaam_engine(
    torch: Any, settings: Settings, spec: ModelSpec, plan: VramPlan | None
) -> GigaAMEngine:
    """Download (or read from ``HF_HOME``), load and warm up one GigaAM variant."""
    from transformers import AutoModel  # noqa: PLC0415  (heavy, imported lazily)

    started = time.monotonic()
    logger.info(
        "Loading %s variant %s (revision %s) on %s",
        settings.model_id,
        spec.variant,
        spec.revision,
        settings.device,
    )
    model = AutoModel.from_pretrained(
        settings.model_id, revision=spec.revision, trust_remote_code=True
    )
    model.eval()
    fp16 = settings.fp16_encoder and settings.device_type == "cuda"
    if fp16:
        # Only the encoder: the mel front-end and the CTC head must stay in float32
        # (never call model.half()). Halving before the transfer also halves the copy.
        model.model.encoder = model.model.encoder.half()
    model.to(settings.device)

    info = EngineInfo(
        model_id=settings.model_id,
        variant=spec.variant,
        revision=spec.revision,
        device=settings.device,
        fp16_encoder=fp16,
        vram_limit_gb=None if plan is None else round(plan.budget_bytes / GIB, 2),
        vram_free_gb_at_start=None if plan is None else round(plan.free_bytes / GIB, 2),
    )
    engine = GigaAMEngine(torch, model, info)
    try:
        engine.warm_up(settings)
    except Exception as exc:
        if not fp16 or is_out_of_memory(torch, exc):
            raise
        error = f"{type(exc).__name__}: {exc}"
    else:
        error = None
    if error is not None:
        logger.warning(
            "Warm-up with an fp16 encoder failed (%s); retrying in float32", error
        )
        model.model.encoder = model.model.encoder.float()
        release_cuda_memory(torch)
        engine.info = replace(info, fp16_encoder=False)
        engine.warm_up(settings)

    logger.info(
        "Loaded %s (revision %s) on %s in %.1f s (fp16 encoder: %s)",
        spec.variant,
        spec.revision,
        settings.device,
        time.monotonic() - started,
        engine.info.fp16_encoder,
    )
    return engine


class GigaAMEngine:
    """Batched GigaAM CTC inference. Not thread-safe: call it from one thread."""

    def __init__(self, torch: Any, model: Any, info: EngineInfo):
        """Wrap a loaded ``AutoModel`` (``model.model`` is the GigaAMASR module)."""
        self._torch = torch
        self._asr = model.model
        self._device = torch.device(info.device)
        self.info = info

    def warm_up(self, settings: Settings) -> None:
        """Run a first forward pass and, on CUDA, the largest allowed batch.

        The first pass builds the lazily created rotary table before serving.
        The largest batch reserves the memory needed later and turns a budget that is
        too small into an out-of-memory error at startup rather than at serving time.
        """
        self.transcribe_batch(
            [np.zeros(SAMPLE_RATE, dtype=np.float32)], split_on_oom=False
        )
        if self._device.type != "cuda":
            return
        length = int(settings.max_audio_seconds * SAMPLE_RATE)
        count = max(
            1,
            min(
                settings.max_batch_size,
                int(settings.max_batch_audio_seconds // settings.max_audio_seconds),
            ),
        )
        rng = np.random.default_rng(0)
        noise = (rng.standard_normal(length) * 0.01).astype(np.float32)
        self.transcribe_batch([noise] * count, split_on_oom=False)
        self._torch.cuda.synchronize(self._device)
        logger.info(
            "Warm-up with %d x %g s done; CUDA memory reserved %.2f GiB",
            count,
            settings.max_audio_seconds,
            self._torch.cuda.memory_reserved(self._device) / GIB,
        )

    def transcribe_batch(
        self, wavs: Sequence[np.ndarray], split_on_oom: bool = True
    ) -> list[str]:
        """Transcribe clips in one forward pass; on CUDA OOM retry them one by one."""
        if not wavs:
            return []
        try:
            return self._forward(wavs)
        except Exception as exc:
            if (
                not split_on_oom
                or len(wavs) == 1
                or not is_out_of_memory(self._torch, exc)
            ):
                raise
        release_cuda_memory(self._torch)
        logger.warning(
            "CUDA out of memory on a batch of %d clips; retrying one by one", len(wavs)
        )
        return [self._forward([wav])[0] for wav in wavs]

    def _forward(self, wavs: Sequence[np.ndarray]) -> list[str]:
        torch = self._torch
        lengths = torch.tensor([int(wav.shape[0]) for wav in wavs], dtype=torch.long)
        batch = torch.zeros((len(wavs), int(lengths.max())), dtype=torch.float32)
        for row, wav in enumerate(wavs):
            batch[row, : wav.shape[0]] = torch.from_numpy(
                np.asarray(wav, dtype=np.float32)
            )
        with torch.inference_mode():
            encoded, encoded_len = self._asr(
                batch.to(self._device), lengths.to(self._device)
            )
            decoded = self._asr._decode(encoded, encoded_len, lengths)
        return [" ".join(text.split()) for text, _words in decoded]
