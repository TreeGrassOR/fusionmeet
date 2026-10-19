"""Tensor glue of GigaAMEngine with a fake model; skipped when torch is absent."""

from types import SimpleNamespace

import numpy as np
import pytest

from asr_service.engine import GIB, EngineInfo, GigaAMEngine, load_engine

from .conftest import FakeEngine, make_settings

torch = pytest.importorskip("torch")


class FakeASR:
    """Mimics GigaAMASR: ``__call__`` returns encoder output, ``_decode`` the texts."""

    def __init__(self, oom_above: int | None = None):
        """Raise a CUDA OOM for batches larger than ``oom_above``."""
        self.batches: list[tuple[torch.Tensor, torch.Tensor]] = []
        self.oom_above = oom_above

    def __call__(self, batch, lengths):
        """Record the padded batch and return fake encodings."""
        if self.oom_above is not None and batch.shape[0] > self.oom_above:
            raise torch.cuda.OutOfMemoryError("CUDA out of memory")
        self.batches.append((batch.clone(), lengths.clone()))
        return batch, (lengths // 640).to(torch.int32)

    def _decode(self, encoded, encoded_len, wav_lens):
        return [(f"  clip   {int(n)} \n", None) for n in wav_lens]


def make_engine(asr: FakeASR) -> GigaAMEngine:
    """Wrap ``asr`` like build_gigaam_engine does."""
    info = EngineInfo(
        "ai-sage/GigaAM-Multilingual", "ctc", "rev", "cpu", fp16_encoder=False
    )
    return GigaAMEngine(torch, SimpleNamespace(model=asr), info)


def test_batch_is_zero_padded_float32_with_int64_lengths():
    """Clips are padded to the longest one; lengths are the real sizes."""
    asr = FakeASR()
    wavs = [
        np.full(1600, 0.5, dtype=np.float32),
        np.full(4000, -0.25, dtype=np.float32),
    ]
    texts = make_engine(asr).transcribe_batch(wavs)
    assert texts == ["clip 1600", "clip 4000"]
    batch, lengths = asr.batches[0]
    assert batch.dtype == torch.float32
    assert tuple(batch.shape) == (2, 4000)
    assert lengths.dtype == torch.int64
    assert lengths.tolist() == [1600, 4000]
    assert torch.all(batch[0, :1600] == 0.5)
    assert torch.all(batch[0, 1600:] == 0)
    assert torch.all(batch[1] == -0.25)


def test_oom_on_a_batch_retries_clip_by_clip():
    """A CUDA OOM on a batch is retried one clip at a time."""
    asr = FakeASR(oom_above=1)
    wavs = [np.zeros(n, dtype=np.float32) for n in (1600, 3200, 4800)]
    assert make_engine(asr).transcribe_batch(wavs) == [
        "clip 1600",
        "clip 3200",
        "clip 4800",
    ]
    assert [tuple(b.shape) for b, _ in asr.batches] == [(1, 1600), (1, 3200), (1, 4800)]


def test_empty_batch():
    """No clips, no forward pass."""
    asr = FakeASR()
    assert make_engine(asr).transcribe_batch([]) == []
    assert asr.batches == []


@pytest.mark.parametrize(("device", "index"), [("cuda", 0), ("cuda:1", 1)])
def test_real_torch_accepts_the_memory_fraction_arguments(monkeypatch, device, index):
    """The real set_per_process_memory_fraction() takes what load_engine passes.

    Only the driver calls are stubbed: torch's own argument checks still run (they
    refuse a device without an index such as "cuda").
    """
    calls = []
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(torch.cuda, "current_device", lambda: 0)
    monkeypatch.setattr(torch.cuda, "mem_get_info", lambda _d: (10 * GIB, 24 * GIB))
    monkeypatch.setattr(torch.cuda.memory, "_lazy_init", lambda: None)
    monkeypatch.setattr(
        torch._C,
        "_cuda_setMemoryFraction",
        lambda fraction, idx: calls.append((fraction, idx)),
        raising=False,
    )
    load_engine(
        make_settings(device=device),
        torch_module=torch,
        build_engine=lambda _s, spec, _p: FakeEngine(variant=spec.variant),
    )
    assert calls == [(pytest.approx(4 / 24), index)]
