"""Eager loading of every enabled model, per-model status and per-model queues.

Every recogniser has its own worker thread, which loads it and then runs all its
inference, and its own micro-batcher: batches of ``ASR_MAX_BATCH_SIZE`` clips on the
GPU, one clip at a time on the CPU. A model that fails to load is marked ``failed``
and the others keep serving.
"""

from __future__ import annotations

import asyncio
import importlib
import logging
import os
import resource
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Protocol

import numpy as np

from .audio import SAMPLE_RATE
from .batcher import MicroBatcher
from .config import Settings
from .engine import (
    Engine,
    EngineInfo,
    GpuLoadError,
    build_gigaam_engine,
    limit_gpu_memory,
    probe_gpu,
)
from .placement import GIB, GpuInfo, Placement, plan_placement
from .registry import (
    MODELS,
    PUNCTUATORS,
    RECOGNIZERS,
    ModelSpec,
    OnnxBackend,
    Punctuator,
    Recognizer,
    TorchBackend,
)
from .text import clean_text

logger = logging.getLogger(__name__)

LOADING = "loading"
READY = "ready"
FAILED = "failed"

Punctuate = Callable[[str], str]


class ModelUnavailableError(Exception):
    """The model is still loading or failed to load."""

    def __init__(self, model: ModelSpec, status: str, error: str | None):
        """Describe why ``model`` cannot serve requests."""
        if status == LOADING:
            message = f"Model {model.id} is still loading"
        else:
            message = f"Model {model.id} failed to load: {error}"
        super().__init__(message)
        self.status = status


class Backends(Protocol):
    """What the manager needs to place and load models (replaced in tests)."""

    def probe_gpu(self, settings: Settings) -> tuple[GpuInfo | None, str | None]:
        """Return the GPU memory, or None and the reason CUDA is not usable."""
        ...

    def limit_gpu_memory(self, gpu: GpuInfo, budget_bytes: int) -> None:
        """Keep this process within ``budget_bytes`` of GPU memory."""
        ...

    def build_gpu(
        self, settings: Settings, backend: TorchBackend, device: str
    ) -> Engine:
        """Load a recogniser on the GPU (raises GpuLoadError)."""
        ...

    def build_cpu(self, settings: Settings, backend: OnnxBackend) -> Engine:
        """Load a recogniser on the CPU."""
        ...

    def build_punctuator(self, settings: Settings, spec: Punctuator) -> Punctuate:
        """Load a punctuation model on the CPU."""
        ...


class RealBackends:
    """torch for the GPU, onnx-asr and onnxruntime for the CPU (imported lazily)."""

    def __init__(self) -> None:
        """Nothing is imported until a model is placed."""
        self._torch: Any = None

    def probe_gpu(self, settings: Settings) -> tuple[GpuInfo | None, str | None]:
        """Import torch, bound its CPU threads and read the GPU memory."""
        try:
            torch = importlib.import_module("torch")
        except ImportError as exc:
            return None, f"torch cannot be imported ({exc})"
        torch.set_num_threads(settings.cpu_threads)
        self._torch = torch
        return probe_gpu(torch, settings)

    def limit_gpu_memory(self, gpu: GpuInfo, budget_bytes: int) -> None:
        """Set the per-process memory fraction of the CUDA caching allocator."""
        limit_gpu_memory(self._torch, gpu, budget_bytes)

    def build_gpu(
        self, settings: Settings, backend: TorchBackend, device: str
    ) -> Engine:
        """Load GigaAM with torch on ``device``."""
        return build_gigaam_engine(self._torch, settings, backend, device)

    def build_cpu(self, settings: Settings, backend: OnnxBackend) -> Engine:
        """Load an onnx-asr model."""
        from .onnx_engine import build_onnx_engine  # noqa: PLC0415

        return build_onnx_engine(backend, settings.cpu_threads)

    def build_punctuator(self, settings: Settings, spec: Punctuator) -> Punctuate:
        """Load the punctuation model."""
        from .punctuation import build_punctuator  # noqa: PLC0415

        return build_punctuator(spec, settings.cpu_threads)


@dataclass
class _Component:
    """A recogniser or a punctuator: its state, worker thread and loaded object."""

    key: str
    executor: ThreadPoolExecutor
    status: str = LOADING
    error: str | None = None
    device: str | None = None
    engine: Engine | None = None
    batcher: MicroBatcher | None = None
    punctuate: Punctuate | None = None
    load_seconds: float | None = None


@dataclass
class _LoadOrder:
    recognizers: list[str] = field(default_factory=list)
    punctuators: list[str] = field(default_factory=list)
    steps: list[tuple[str, str]] = field(default_factory=list)


def _load_order(settings: Settings) -> _LoadOrder:
    """Components of the enabled models, the default model's first."""
    order = _LoadOrder()
    ids = [settings.default_model] + [
        model_id for model_id in settings.models if model_id != settings.default_model
    ]
    for model_id in ids:
        model = MODELS[model_id]
        if model.recognizer not in order.recognizers:
            order.recognizers.append(model.recognizer)
            order.steps.append(("recognizer", model.recognizer))
        if model.punctuator is not None and model.punctuator not in order.punctuators:
            order.punctuators.append(model.punctuator)
            order.steps.append(("punctuator", model.punctuator))
    return order


def _memory_mb() -> tuple[float | None, float]:
    """Current and peak resident memory of this process (MB)."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
    try:
        with open("/proc/self/statm", encoding="ascii") as statm:
            pages = int(statm.read().split()[1])
    except (OSError, IndexError, ValueError):
        return None, peak
    return pages * os.sysconf("SC_PAGE_SIZE") / 1024**2, peak


class ModelManager:
    """Loads the enabled models at startup and routes clips to them."""

    def __init__(self, settings: Settings, backends: Backends):
        """Prepare one worker thread per recogniser and punctuator."""
        self.settings = settings
        self.models = {model_id: MODELS[model_id] for model_id in settings.models}
        self.placement: Placement | None = None
        self._backends = backends
        self._order = _load_order(settings)
        self._recognizers = {
            key: _Component(key, _executor(key)) for key in self._order.recognizers
        }
        self._punctuators = {
            key: _Component(key, _executor(key)) for key in self._order.punctuators
        }

    # ----------------------------------------------------------------- lifecycle

    async def load(self) -> None:
        """Place the recognisers, then load every component in priority order."""
        loop = asyncio.get_running_loop()
        recognizers = [RECOGNIZERS[key] for key in self._order.recognizers]
        try:
            placement = await loop.run_in_executor(
                self._recognizers[self._order.recognizers[0]].executor,
                self._plan,
                recognizers,
            )
        except Exception:
            logger.exception("Placing the models failed; every model runs on the CPU")
            placement = Placement({recognizer.key: "cpu" for recognizer in recognizers})
        self.placement = placement
        for kind, key in self._order.steps:
            if kind == "recognizer":
                await self._load_recognizer(key, placement.devices[key])
            else:
                await self._load_punctuator(key)
        statuses = {
            model_id: self.status(model)[0] for model_id, model in self.models.items()
        }
        logger.info(
            "Model loading finished: %s",
            ", ".join(f"{model_id} {status}" for model_id, status in statuses.items()),
        )

    async def stop(self) -> None:
        """Fail the queued requests and stop the worker threads."""
        for component in self._recognizers.values():
            if component.batcher is not None:
                await component.batcher.stop()
        for component in [*self._recognizers.values(), *self._punctuators.values()]:
            component.executor.shutdown(wait=False, cancel_futures=True)

    def _plan(self, recognizers: list[Recognizer]) -> Placement:
        gpu = reason = None
        if self.settings.device_type == "cuda" and not any(
            recognizer.gpu is not None for recognizer in recognizers
        ):
            # Probing would import torch and create a CUDA context for nothing.
            logger.info("No enabled model has a GPU backend: the GPU is not used")
        elif self.settings.device_type == "cuda":
            try:
                gpu, reason = self._backends.probe_gpu(self.settings)
            except Exception as exc:
                gpu, reason = (
                    None,
                    f"the GPU cannot be used ({type(exc).__name__}: {exc})",
                )
        placement = plan_placement(self.settings, recognizers, gpu, reason)
        if placement.uses_gpu and placement.gpu is not None:
            self._backends.limit_gpu_memory(placement.gpu, placement.budget_bytes)
        return placement

    async def _load_recognizer(self, key: str, device: str) -> None:
        loop = asyncio.get_running_loop()
        component = self._recognizers[key]
        recognizer = RECOGNIZERS[key]
        started = time.monotonic()
        try:
            engine = None
            if device != "cpu" and recognizer.gpu is not None:
                try:
                    engine = await loop.run_in_executor(
                        component.executor,
                        self._backends.build_gpu,
                        self.settings,
                        recognizer.gpu,
                        device,
                    )
                except GpuLoadError as exc:
                    # Out of memory, a CUDA or a download error: the CPU backend
                    # can still serve the model.
                    logger.warning("%s; %s runs on the CPU instead", exc, key)
            if engine is None:
                device = "cpu"
                engine = await loop.run_in_executor(
                    component.executor,
                    self._backends.build_cpu,
                    self.settings,
                    recognizer.cpu,
                )
        except Exception as exc:
            self._fail(component, exc)
            return
        component.batcher = self._batcher(engine, component.executor, device)
        component.batcher.start()
        component.engine = engine
        component.device = device.split(":", 1)[0]
        self._ready(component, started)

    async def _load_punctuator(self, key: str) -> None:
        loop = asyncio.get_running_loop()
        component = self._punctuators[key]
        started = time.monotonic()
        try:
            component.punctuate = await loop.run_in_executor(
                component.executor,
                self._backends.build_punctuator,
                self.settings,
                PUNCTUATORS[key],
            )
        except Exception as exc:
            self._fail(component, exc)
            return
        component.device = "cpu"
        self._ready(component, started)

    def _batcher(
        self, engine: Engine, executor: ThreadPoolExecutor, device: str
    ) -> MicroBatcher:
        settings = self.settings
        if device == "cpu":
            # One clip per call (see OnnxAsrEngine): batching would only delay.
            return MicroBatcher(
                engine.transcribe_batch,
                executor,
                max_batch_size=1,
                max_wait_s=0,
                max_batch_samples=int(settings.max_audio_seconds * SAMPLE_RATE),
                max_queue=settings.max_queue,
            )
        return MicroBatcher(
            engine.transcribe_batch,
            executor,
            max_batch_size=settings.max_batch_size,
            max_wait_s=settings.batch_wait_ms / 1000,
            max_batch_samples=int(settings.max_batch_audio_seconds * SAMPLE_RATE),
            max_queue=settings.max_queue,
        )

    def _fail(self, component: _Component, exc: Exception) -> None:
        component.status = FAILED
        component.error = f"{type(exc).__name__}: {exc}"
        users = self._users(component.key)
        logger.error(
            "Failed to load %s: %s. Model(s) %s will answer 503; the others keep "
            "serving. Restart the service to retry.",
            component.key,
            component.error,
            ", ".join(users),
            exc_info=exc,
        )

    def _ready(self, component: _Component, started: float) -> None:
        component.status = READY
        component.load_seconds = round(time.monotonic() - started, 1)
        rss, peak = _memory_mb()
        logger.info(
            "%s ready on %s in %.1f s (model(s) %s); process RSS %s MB, peak %.0f MB",
            component.key,
            component.device,
            component.load_seconds,
            ", ".join(self._users(component.key)),
            "?" if rss is None else f"{rss:.0f}",
            peak,
        )

    def _users(self, key: str) -> list[str]:
        return [
            model.id
            for model in self.models.values()
            if key in (model.recognizer, model.punctuator)
        ]

    # ------------------------------------------------------------------ queries

    def status(self, model: ModelSpec) -> tuple[str, str | None]:
        """Return ``(status, error)`` of a model from those of its components."""
        components = [self._recognizers[model.recognizer]]
        if model.punctuator is not None:
            components.append(self._punctuators[model.punctuator])
        for component in components:
            if component.status == FAILED:
                return FAILED, component.error
        if all(component.status == READY for component in components):
            return READY, None
        return LOADING, None

    def device(self, model: ModelSpec) -> str | None:
        """Device of the model's recogniser ("cuda" or "cpu") once it is ready."""
        if self.status(model)[0] != READY:
            return None
        return self._recognizers[model.recognizer].device

    def pending(self, model: ModelSpec) -> int:
        """Clips waiting in the queue of the model's recogniser."""
        batcher = self._recognizers[model.recognizer].batcher
        return 0 if batcher is None else batcher.pending

    def engine_info(self, model: ModelSpec) -> EngineInfo | None:
        """Description of the model's recogniser once it is loaded."""
        engine = self._recognizers[model.recognizer].engine
        return None if engine is None else engine.info

    def describe(self) -> dict[str, Any]:
        """Body of ``GET /v1/models``."""
        models = []
        for model in self.models.values():
            status, error = self.status(model)
            models.append(
                {
                    "id": model.id,
                    "label": model.label,
                    "description": model.description,
                    "languages": list(model.languages),
                    "punctuation": model.punctuation,
                    "device": self.device(model),
                    "status": status,
                    "error": error,
                }
            )
        return {"default": self.settings.default_model, "models": models}

    def health(self) -> dict[str, Any]:
        """Body of ``GET /health``; ``status`` is "ok" once the default is ready."""
        default_status, _ = self.status(self.models[self.settings.default_model])
        placement = self.placement
        models = []
        for model in self.models.values():
            entry: dict[str, Any] = {
                "id": model.id,
                "status": self.status(model)[0],
                "device": self.device(model),
            }
            info = self.engine_info(model)
            if info is not None:
                entry.update(
                    model=info.model_id,
                    revision=info.revision,
                    variant=info.variant,
                    fp16_encoder=info.fp16_encoder,
                )
            models.append(entry)
        gpu = None if placement is None else placement.gpu
        return {
            "status": "ok" if default_status == READY else default_status,
            "default": self.settings.default_model,
            "models": models,
            "vram_limit_gb": None
            if placement is None or not placement.uses_gpu
            else round(placement.budget_bytes / GIB, 2),
            "vram_free_gb_at_start": None
            if gpu is None
            else round(gpu.free_bytes / GIB, 2),
            "cpu_threads": self.settings.cpu_threads,
            "max_batch_size": self.settings.max_batch_size,
        }

    # ---------------------------------------------------------------- inference

    def check_available(self, model: ModelSpec) -> None:
        """Raise ModelUnavailableError unless the model is ready."""
        status, error = self.status(model)
        if status != READY:
            raise ModelUnavailableError(model, status, error)

    async def transcribe(
        self, model: ModelSpec, wav: np.ndarray
    ) -> tuple[str, EngineInfo]:
        """Recognise one clip with ``model``; return its cleaned text and EngineInfo.

        Raises:
            ModelUnavailableError: the model is not ready.
            QueueFullError, BatcherClosedError: from the model's queue.
        """
        self.check_available(model)
        recognizer = self._recognizers[model.recognizer]
        batcher, engine = recognizer.batcher, recognizer.engine
        if batcher is None or engine is None:  # pragma: no cover  (ready implies both)
            raise ModelUnavailableError(model, LOADING, None)
        text = clean_text(await batcher.submit(wav))
        if model.punctuator is not None and text:
            punctuator = self._punctuators[model.punctuator]
            loop = asyncio.get_running_loop()
            try:
                text = await loop.run_in_executor(
                    punctuator.executor, punctuator.punctuate, text
                )
            except Exception:
                logger.exception("Punctuation with %s failed", punctuator.key)
                raise
            text = clean_text(text)
        return text, engine.info


def _executor(key: str) -> ThreadPoolExecutor:
    return ThreadPoolExecutor(max_workers=1, thread_name_prefix=f"asr-{key}")
