"""Recognisers on the CPU: ONNX exports run by onnx-asr on onnxruntime.

``onnx_asr``, ``onnxruntime`` and ``huggingface_hub`` are imported lazily, so the
HTTP layer and the unit tests run without them.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import numpy as np

from .audio import SAMPLE_RATE
from .engine import EngineInfo
from .registry import OnnxBackend

logger = logging.getLogger(__name__)


def cpu_session_options(threads: int) -> Any:
    """Session options: ``threads`` intra-op threads, sequential execution."""
    import onnxruntime  # noqa: PLC0415  (heavy, imported lazily)

    options = onnxruntime.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.execution_mode = onnxruntime.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = onnxruntime.GraphOptimizationLevel.ORT_ENABLE_ALL
    return options


def download_snapshot(repo: str, revision: str, files: Sequence[str]) -> Path:
    """Download ``files`` of ``repo`` at a pinned revision; return their directory.

    onnx-asr has no revision parameter, so the files are fetched here and onnx-asr
    reads them from the returned directory. They are plain files in
    ``$HF_HOME/onnx/<owner>--<name>/<revision>``, not the symlinks of the Hugging
    Face cache: onnxruntime refuses external weights (``*.onnx.data``)
    that resolve outside the directory of their ``.onnx`` file, and in the cache
    every file resolves to its own blob. Later starts read them without any request
    to the Hub.
    """
    from huggingface_hub import constants, snapshot_download  # noqa: PLC0415

    local_dir = Path(constants.HF_HOME, "onnx", repo.replace("/", "--"), revision)
    return Path(
        snapshot_download(
            repo, revision=revision, allow_patterns=list(files), local_dir=local_dir
        )
    )


def build_onnx_engine(backend: OnnxBackend, threads: int) -> OnnxAsrEngine:
    """Download (or read from ``HF_HOME``), load and warm up one onnx-asr model."""
    import onnx_asr  # noqa: PLC0415  (heavy, imported lazily)

    started = time.monotonic()
    logger.info(
        "Loading %s (revision %s) on cpu, %d thread(s)",
        backend.repo,
        backend.revision,
        threads,
    )
    path = download_snapshot(backend.repo, backend.revision, backend.files)
    model = onnx_asr.load_model(
        backend.model_type,
        path,
        quantization=backend.quantization,
        sess_options=cpu_session_options(threads),
        providers=["CPUExecutionProvider"],
    )
    info = EngineInfo(
        model_id=backend.repo,
        variant=backend.variant,
        revision=backend.revision,
        device="cpu",
    )
    engine = OnnxAsrEngine(model, info)
    engine.transcribe_batch([np.zeros(SAMPLE_RATE, dtype=np.float32)])
    logger.info(
        "Loaded %s (revision %s) on cpu in %.1f s",
        backend.variant,
        backend.revision,
        time.monotonic() - started,
    )
    return engine


class OnnxAsrEngine:
    """onnx-asr inference, one clip per call. Not thread-safe: use one thread."""

    def __init__(self, model: Any, info: EngineInfo):
        """Wrap a model returned by ``onnx_asr.load_model``."""
        self._model = model
        self.info = info

    def transcribe_batch(self, wavs: Sequence[np.ndarray]) -> list[str]:
        """Transcribe the clips one at a time.

        Never as one padded batch: in the GigaAM-v3 exports the padding leaks into
        the output (other words, lost final periods), and for the other models a
        batch was slower than single clips on the CPU.
        """
        return [
            self._model.recognize(
                np.asarray(wav, dtype=np.float32), sample_rate=SAMPLE_RATE
            )
            for wav in wavs
        ]
