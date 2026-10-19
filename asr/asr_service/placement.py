"""Which recogniser runs on the GPU and which on the CPU.

With ``ASR_DEVICE=cuda`` the recognisers that have a GPU backend go to the GPU, in
priority order (the default model's first), while their VRAM needs fit in the budget
``min(ASR_MAX_VRAM_GB, free memory after the CUDA context)``. The others, and all of
them when CUDA is not usable, run on the CPU; a WARNING says why. With
``ASR_DEVICE=cpu`` everything runs on the CPU.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field

from .config import Settings
from .registry import Recognizer

logger = logging.getLogger(__name__)

GIB = 1024**3


@dataclass(frozen=True)
class GpuInfo:
    """Memory of the GPU named by ASR_DEVICE, read after the CUDA context exists."""

    index: int
    free_bytes: int
    total_bytes: int


@dataclass(frozen=True)
class Placement:
    """Device of each recogniser ("cuda:<n>" or "cpu") and the GPU budget."""

    devices: dict[str, str] = field(default_factory=dict)
    gpu: GpuInfo | None = None
    budget_bytes: int | None = None

    @property
    def uses_gpu(self) -> bool:
        """Tell whether at least one recogniser is placed on the GPU."""
        return any(device != "cpu" for device in self.devices.values())

    @property
    def memory_fraction(self) -> float | None:
        """Fraction passed to ``torch.cuda.set_per_process_memory_fraction``."""
        if self.gpu is None or self.budget_bytes is None:
            return None
        return self.budget_bytes / self.gpu.total_bytes


def plan_placement(
    settings: Settings,
    recognizers: Sequence[Recognizer],
    gpu: GpuInfo | None,
    no_gpu_reason: str | None = None,
) -> Placement:
    """Place ``recognizers`` (in priority order) on the GPU or the CPU.

    Args:
        settings: the service settings (``ASR_DEVICE``, budget, VRAM needs).
        recognizers: the recognisers to load, most important first.
        gpu: the GPU memory, or None when CUDA is not usable.
        no_gpu_reason: why CUDA is not usable, for the warning.
    """
    if settings.device_type == "cpu":
        logger.info("ASR_DEVICE=cpu: every model runs on the CPU")
        return Placement({recognizer.key: "cpu" for recognizer in recognizers})

    if gpu is None:
        gpu_capable = [r.key for r in recognizers if r.gpu is not None]
        if gpu_capable:
            logger.warning(
                "ASR_DEVICE=%s but %s: %s run(s) on the CPU instead (slower)",
                settings.device,
                no_gpu_reason or "CUDA is not available",
                ", ".join(gpu_capable),
            )
        return Placement({recognizer.key: "cpu" for recognizer in recognizers})

    budget = min(int(settings.max_vram_gb * GIB), int(gpu.free_bytes))
    remaining = budget
    devices = {}
    logger.info(
        "VRAM budget %.2f GiB (free %.2f of %.2f GiB on cuda:%d, ASR_MAX_VRAM_GB=%g)",
        budget / GIB,
        gpu.free_bytes / GIB,
        gpu.total_bytes / GIB,
        gpu.index,
        settings.max_vram_gb,
    )
    for recognizer in recognizers:
        if recognizer.gpu is None:
            logger.info("%s has no GPU backend: it runs on the CPU", recognizer.key)
            devices[recognizer.key] = "cpu"
            continue
        need = int(settings.model_vram_gb(recognizer.key) * GIB)
        if need <= remaining:
            devices[recognizer.key] = f"cuda:{gpu.index}"
            remaining -= need
            logger.info(
                "%s runs on cuda:%d (needs %.2f GiB, %.2f GiB of the budget left)",
                recognizer.key,
                gpu.index,
                need / GIB,
                remaining / GIB,
            )
        else:
            devices[recognizer.key] = "cpu"
            logger.warning(
                "Not enough VRAM for %s on the GPU: it needs %.2f GiB but only "
                "%.2f GiB of the %.2f GiB budget is left (free %.2f of %.2f GiB, "
                "ASR_MAX_VRAM_GB=%g). It runs on the CPU instead (slower); free GPU "
                "memory or raise ASR_MAX_VRAM_GB to run it on the GPU",
                recognizer.key,
                need / GIB,
                remaining / GIB,
                budget / GIB,
                gpu.free_bytes / GIB,
                gpu.total_bytes / GIB,
                settings.max_vram_gb,
            )
    return Placement(devices, gpu, budget)
