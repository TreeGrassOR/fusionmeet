"""GigaAM on the GPU (torch): CUDA probing, model loading and batched inference.

``torch`` and ``transformers`` are imported lazily, inside the functions that need
them, so the HTTP layer and the unit tests run without them.
"""

from __future__ import annotations

import gc
import logging
import os
import time
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

import numpy as np

from .audio import SAMPLE_RATE
from .config import Settings
from .placement import GIB, GpuInfo
from .registry import TorchBackend

logger = logging.getLogger(__name__)

# Clips shorter than this are answered with "" without running the model: torch.stft
# fails on fewer than 320 samples, and 0.1 s cannot hold a word anyway.
MIN_SAMPLES = SAMPLE_RATE // 10


class GpuLoadError(RuntimeError):
    """A model could not be loaded on the GPU (the memory it took was freed)."""


class GpuOutOfMemoryError(GpuLoadError):
    """CUDA ran out of memory while a model was loading (its memory was freed)."""


@dataclass(frozen=True)
class EngineInfo:
    """Description of a loaded recogniser, reported by ``/health`` and responses."""

    model_id: str
    variant: str
    revision: str
    device: str
    fp16_encoder: bool = False


class Engine(Protocol):
    """What the HTTP layer needs from a loaded recogniser."""

    info: EngineInfo

    def transcribe_batch(self, wavs: Sequence[np.ndarray]) -> list[str]:
        """Transcribe mono float32 16 kHz clips; return one text per clip."""
        ...


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


def probe_gpu(torch: Any, settings: Settings) -> tuple[GpuInfo | None, str | None]:
    """Read the free memory of the ASR_DEVICE GPU (this creates the CUDA context).

    Returns:
        ``(info, None)``, or ``(None, reason)`` when CUDA is not usable.
    """
    if not torch.cuda.is_available():
        return None, (
            "CUDA is not available in this container (check the NVIDIA driver, the "
            "NVIDIA Container Toolkit and the GPU reservation of the asr service)"
        )
    # set_per_process_memory_fraction() raises ValueError for a device without an
    # index ("cuda"), so resolve it to the current device first.
    index = settings.device_index
    if index is None:
        index = torch.cuda.current_device()
    free_bytes, total_bytes = torch.cuda.mem_get_info(index)
    return GpuInfo(index, int(free_bytes), int(total_bytes)), None


def limit_gpu_memory(torch: Any, gpu: GpuInfo, budget_bytes: int) -> None:
    """Keep the CUDA caching allocator of this process within the budget."""
    fraction = budget_bytes / gpu.total_bytes
    torch.cuda.set_per_process_memory_fraction(fraction, gpu.index)
    logger.info(
        "Limited the CUDA caching allocator of this process to %.2f GiB "
        "(fraction %.4f of %.2f GiB)",
        budget_bytes / GIB,
        fraction,
        gpu.total_bytes / GIB,
    )


def load_gigaam_model(backend: TorchBackend) -> Any:
    """Download (or read from ``HF_HOME``) and instantiate a GigaAM model on the CPU.

    Without ``code_repo`` the weights' own remote code is used (``AutoModel``).
    With it, the weights are cross-loaded into the ``GigaAMModel`` class of
    ``code_repo``: GigaAM-v3 through the GigaAM-Multilingual class, because the v3
    remote code does not load on transformers 5.20.1.
    """
    # The weights are pytorch_model.bin files. Without this, transformers starts a
    # background download of the safetensors conversion from the Hub converter
    # bot's pull request (an unpinned revision), which is never used.
    os.environ.setdefault("DISABLE_SAFETENSORS_CONVERSION", "true")
    if backend.code_repo is None:
        from transformers import AutoModel  # noqa: PLC0415  (heavy, imported lazily)

        return AutoModel.from_pretrained(
            backend.repo, revision=backend.revision, trust_remote_code=True
        )

    from transformers.dynamic_module_utils import (  # noqa: PLC0415
        get_class_from_dynamic_module,
    )

    model_class = get_class_from_dynamic_module(
        "modeling_gigaam.GigaAMModel",
        backend.code_repo,
        revision=backend.code_revision,
    )
    config = model_class.config_class.from_pretrained(
        backend.repo, revision=backend.revision
    )
    # GigaAMModel.__init__ looks up tokenizer.model with
    # revision=config._commit_hash, which transformers 5.20.1 no longer sets: the
    # lookup would then go to the "main" branch (an OSError when offline).
    config._commit_hash = backend.revision
    return model_class.from_pretrained(
        backend.repo, revision=backend.revision, config=config
    )


def build_gigaam_engine(
    torch: Any, settings: Settings, backend: TorchBackend, device: str
) -> GigaAMEngine:
    """Load one GigaAM model on ``device`` and warm it up.

    Raises:
        GpuOutOfMemoryError: CUDA ran out of memory.
        GpuLoadError: any other failure (download, CUDA or driver error...), logged
            here with its traceback. In both cases the memory has been released.
    """
    started = time.monotonic()
    logger.info(
        "Loading %s (revision %s) on %s", backend.repo, backend.revision, device
    )
    model = None
    try:
        model = load_gigaam_model(backend)
        engine = _prepare(torch, settings, backend, device, model)
    except Exception as exc:
        out_of_memory = is_out_of_memory(torch, exc)
        if not out_of_memory:
            logger.error("Loading %s on %s failed", backend.repo, device, exc_info=True)
        error = f"{type(exc).__name__}: {exc}"
    else:
        logger.info(
            "Loaded %s (revision %s) on %s in %.1f s (fp16 encoder: %s)",
            backend.variant,
            backend.revision,
            device,
            time.monotonic() - started,
            engine.info.fp16_encoder,
        )
        return engine
    # Outside the except block, so that the traceback and the half-loaded model it
    # references are released before the cache is emptied.
    del model
    release_cuda_memory(torch)
    if out_of_memory:
        raise GpuOutOfMemoryError(
            f"CUDA out of memory while loading {backend.repo} on {device}: {error}"
        )
    raise GpuLoadError(f"Loading {backend.repo} on {device} failed: {error}")


def _prepare(
    torch: Any, settings: Settings, backend: TorchBackend, device: str, model: Any
) -> GigaAMEngine:
    """Move a loaded model to ``device`` (fp16 encoder on CUDA) and warm it up."""
    model.eval()
    fp16 = settings.fp16_encoder and device.startswith("cuda")
    if fp16:
        # Only the encoder: the mel front-end and the CTC/RNN-T heads must stay in
        # float32 (never call model.half()). Halving before the transfer also halves
        # the copy.
        model.model.encoder = model.model.encoder.half()
    model.to(device)

    info = EngineInfo(
        model_id=backend.repo,
        variant=backend.variant,
        revision=backend.revision,
        device=device,
        fp16_encoder=fp16,
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
            "Warm-up of %s with an fp16 encoder failed (%s); retrying in float32",
            backend.variant,
            error,
        )
        model.model.encoder = model.model.encoder.float()
        release_cuda_memory(torch)
        engine.info = replace(info, fp16_encoder=False)
        engine.warm_up(settings)
    return engine


class GigaAMEngine:
    """Batched GigaAM CTC or RNN-T inference. Not thread-safe: use one thread."""

    def __init__(self, torch: Any, model: Any, info: EngineInfo):
        """Wrap a loaded ``GigaAMModel`` (``model.model`` is the GigaAMASR module)."""
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
