"""Tests of the micro-batcher itself."""

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from asr_service.batcher import BatcherClosedError, MicroBatcher

from .conftest import FakeEngine


def clip(samples: int) -> np.ndarray:
    """Return a silent clip of ``samples`` samples."""
    return np.zeros(samples, dtype=np.float32)


@pytest.fixture
def executor():
    """A single worker thread, as in the service."""
    pool = ThreadPoolExecutor(max_workers=1)
    yield pool
    pool.shutdown(wait=True)


def make_batcher(engine, executor, **overrides):
    """Build and start a batcher around ``engine``."""
    options = {
        "max_batch_size": 8,
        "max_wait_s": 0.05,
        "max_batch_samples": 10_000_000,
        "max_queue": 64,
    }
    options.update(overrides)
    batcher = MicroBatcher(engine.transcribe_batch, executor, **options)
    batcher.start()
    return batcher


async def test_padded_size_limit_splits_batches(executor):
    """A batch never exceeds max_batch_samples once padded to its longest clip."""
    engine = FakeEngine()
    batcher = make_batcher(engine, executor, max_batch_samples=10_000)
    lengths = [2000, 2000, 4000, 2000, 2000]
    texts = await asyncio.gather(*(batcher.submit(clip(n)) for n in lengths))
    await batcher.stop()
    assert texts == [f"clip {n}" for n in lengths]
    for call in engine.calls:
        assert max(call) * len(call) <= 10_000
    assert sorted(n for call in engine.calls for n in call) == sorted(lengths)


async def test_single_request_is_not_delayed_by_more_than_the_window(executor):
    """A lone request is sent after at most max_wait_s."""
    engine = FakeEngine()
    batcher = make_batcher(engine, executor, max_wait_s=0.01)
    assert await asyncio.wait_for(batcher.submit(clip(1600)), timeout=1) == "clip 1600"
    await batcher.stop()


async def test_cancelled_requests_are_skipped(executor):
    """A request cancelled while queued does not reach the model."""
    gate = threading.Event()
    engine = FakeEngine(gate=gate)
    batcher = make_batcher(engine, executor, max_batch_size=1, max_wait_s=0)
    first = asyncio.create_task(batcher.submit(clip(1000)))
    while not engine.entered.is_set():
        await asyncio.sleep(0.005)
    abandoned = asyncio.create_task(batcher.submit(clip(2000)))
    kept = asyncio.create_task(batcher.submit(clip(3000)))
    await asyncio.sleep(0.01)
    abandoned.cancel()
    gate.set()
    assert await first == "clip 1000"
    assert await kept == "clip 3000"
    await batcher.stop()
    assert engine.calls == [[1000], [3000]]


async def test_stop_fails_pending_requests(executor):
    """Requests still queued at shutdown get an error instead of hanging."""
    gate = threading.Event()
    engine = FakeEngine(gate=gate)
    batcher = make_batcher(engine, executor, max_batch_size=1, max_wait_s=0)
    inflight = asyncio.create_task(batcher.submit(clip(1000)))
    while not engine.entered.is_set():
        await asyncio.sleep(0.005)
    queued = asyncio.create_task(batcher.submit(clip(2000)))
    await asyncio.sleep(0.01)
    await batcher.stop()
    gate.set()
    for task in (inflight, queued):
        with pytest.raises(BatcherClosedError):
            await task
    with pytest.raises(BatcherClosedError):
        await batcher.submit(clip(1000))


async def test_stop_fails_a_batch_that_is_still_collecting(executor):
    """A clip already taken from the queue for a batch that is not full yet."""
    engine = FakeEngine()
    batcher = make_batcher(engine, executor, max_wait_s=30)
    waiting = asyncio.create_task(batcher.submit(clip(1600)))
    while batcher.pending or not batcher._inflight:
        await asyncio.sleep(0.005)
    await batcher.stop()
    with pytest.raises(BatcherClosedError):
        await asyncio.wait_for(waiting, timeout=1)
    assert engine.calls == []


async def test_wrong_number_of_texts_fails_the_batch(executor):
    """A model returning the wrong number of texts fails the requests loudly."""

    class Broken:
        def transcribe_batch(self, wavs):
            return []

    batcher = MicroBatcher(
        Broken().transcribe_batch,
        executor,
        max_batch_size=8,
        max_wait_s=0,
        max_batch_samples=10_000_000,
        max_queue=8,
    )
    batcher.start()
    with pytest.raises(RuntimeError, match="returned 0 texts for 1 clips"):
        await batcher.submit(clip(1600))
    await batcher.stop()
