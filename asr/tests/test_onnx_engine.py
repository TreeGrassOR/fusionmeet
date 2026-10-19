"""Tests of the onnx-asr CPU backend with fake onnx_asr, onnxruntime and Hub modules."""

import sys
import types
from pathlib import Path

import numpy as np
import pytest

from asr_service.engine import EngineInfo
from asr_service.onnx_engine import OnnxAsrEngine, build_onnx_engine
from asr_service.registry import RECOGNIZERS


class FakeOnnxModel:
    """Plays the adapter returned by onnx_asr.load_model."""

    def __init__(self):
        """No call yet."""
        self.calls = []

    def recognize(self, waveform, sample_rate=16000):
        """Record the call; refuse batches like a v3 export should never get."""
        assert not isinstance(waveform, list), "clips must be sent one at a time"
        assert waveform.dtype == np.float32
        self.calls.append((waveform.shape[0], sample_rate))
        return f"text {waveform.shape[0]}"


@pytest.fixture
def fakes(monkeypatch, tmp_path):
    """Fake onnx_asr, onnxruntime and huggingface_hub; return what they record."""
    record = {"download": [], "load": [], "model": FakeOnnxModel()}

    hub = types.ModuleType("huggingface_hub")
    hub.constants = types.SimpleNamespace(HF_HOME=str(tmp_path))

    def snapshot_download(repo, revision=None, allow_patterns=None, local_dir=None):
        record["download"].append((repo, revision, allow_patterns, local_dir))
        return str(local_dir)

    hub.snapshot_download = snapshot_download

    onnx_asr = types.ModuleType("onnx_asr")

    def load_model(model, path, **kwargs):
        record["load"].append((model, path, kwargs))
        return record["model"]

    onnx_asr.load_model = load_model

    ort = types.ModuleType("onnxruntime")

    class SessionOptions:
        pass

    ort.SessionOptions = SessionOptions
    ort.ExecutionMode = types.SimpleNamespace(ORT_SEQUENTIAL="sequential")
    ort.GraphOptimizationLevel = types.SimpleNamespace(ORT_ENABLE_ALL="all")

    monkeypatch.setitem(sys.modules, "huggingface_hub", hub)
    monkeypatch.setitem(sys.modules, "onnx_asr", onnx_asr)
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)
    return record


def test_gigaam_v3_onnx_is_int8_at_a_pinned_revision(fakes, tmp_path):
    """The v3 export: pinned revision, only the int8 files, CPU provider, threads."""
    backend = RECOGNIZERS["gigaam-v3"].cpu
    engine = build_onnx_engine(backend, threads=3)
    revision = "322c3b29492673eb7d0b434bfa9dfb8653e34d02"
    local_dir = Path(tmp_path, "onnx", "istupakov--gigaam-v3-onnx", revision)
    assert fakes["download"] == [
        (
            "istupakov/gigaam-v3-onnx",
            revision,
            [
                "config.json",
                "v3_e2e_rnnt_encoder.int8.onnx",
                "v3_e2e_rnnt_decoder.int8.onnx",
                "v3_e2e_rnnt_joint.int8.onnx",
                "v3_e2e_rnnt_vocab.txt",
            ],
            local_dir,
        )
    ]
    [(model, path, kwargs)] = fakes["load"]
    assert model == "gigaam-v3-e2e-rnnt"
    assert path == local_dir
    assert kwargs["quantization"] == "int8"
    assert kwargs["providers"] == ["CPUExecutionProvider"]
    options = kwargs["sess_options"]
    assert options.intra_op_num_threads == 3
    assert options.inter_op_num_threads == 1
    assert options.execution_mode == "sequential"
    # warmed up with one second of silence
    assert fakes["model"].calls == [(16000, 16000)]
    assert engine.info == EngineInfo(
        model_id="istupakov/gigaam-v3-onnx",
        variant="v3_e2e_rnnt-onnx-int8",
        revision=revision,
        device="cpu",
    )


def test_parakeet_is_fp32(fakes):
    """Parakeet never uses the int8 encoder: no quantization, fp32 files only."""
    backend = RECOGNIZERS["parakeet-v3"].cpu
    build_onnx_engine(backend, threads=2)
    [(_repo, revision, files, _dir)] = fakes["download"]
    assert revision == "8f23f0c03c8761650bdb5b40aaf3e40d2c15f1ce"
    assert not any("int8" in name for name in files)
    assert "encoder-model.onnx.data" in files
    assert fakes["load"][0][0] == "nemo-parakeet-tdt-0.6b-v3"
    assert fakes["load"][0][2]["quantization"] is None


def test_multilingual_onnx_is_int8(fakes):
    """The legacy model on the CPU is the int8 export."""
    build_onnx_engine(RECOGNIZERS["gigaam-multilingual"].cpu, threads=2)
    [(repo, revision, files, _dir)] = fakes["download"]
    assert repo == "istupakov/gigaam-multilingual-large-ctc-onnx"
    assert revision == "07665ab5e54371dd1ac7b8b10f06478003723573"
    assert "multilingual_large_ctc.int8.onnx" in files
    assert fakes["load"][0][2]["quantization"] == "int8"


def test_clips_are_recognised_one_at_a_time():
    """A batch from the queue becomes one recognize() call per clip."""
    model = FakeOnnxModel()
    info = EngineInfo("repo", "variant", "rev", "cpu")
    engine = OnnxAsrEngine(model, info)
    wavs = [np.zeros(n, dtype=np.float32) for n in (1600, 3200, 4800)]
    assert engine.transcribe_batch(wavs) == ["text 1600", "text 3200", "text 4800"]
    assert model.calls == [(1600, 16000), (3200, 16000), (4800, 16000)]
