"""Asynchronous micro-batcher: concurrent requests share one model call."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable, Sequence
from concurrent.futures import Executor
from dataclasses import dataclass

import numpy as np

from .audio import SAMPLE_RATE

logger = logging.getLogger(__name__)

TranscribeBatch = Callable[[Sequence[np.ndarray]], list[str]]


class QueueFullError(Exception):
    """Too many requests are already waiting for the model."""


class BatcherClosedError(RuntimeError):
    """The batcher is not running (startup not finished or shutting down)."""


@dataclass
class _Item:
    wav: np.ndarray
    future: asyncio.Future[str]


class MicroBatcher:
    """Collect queued clips into batches and run them on a single worker thread.

    A batch is closed when it holds ``max_batch_size`` clips, when ``max_wait_s`` has
    elapsed since its first clip was taken, or when one more clip would make the
    padded batch (clips x longest clip) exceed ``max_batch_samples``. Requests that
    arrive while the model is busy wait in the queue and form the next batch.
    """

    def __init__(  # noqa: PLR0913
        self,
        transcribe: TranscribeBatch,
        executor: Executor,
        *,
        max_batch_size: int,
        max_wait_s: float,
        max_batch_samples: int,
        max_queue: int,
    ):
        """Configure the batcher; call ``start()`` from a running event loop."""
        self._transcribe = transcribe
        self._executor = executor
        self._max_batch_size = max_batch_size
        self._max_wait_s = max_wait_s
        self._max_batch_samples = max_batch_samples
        self._queue: asyncio.Queue[_Item] = asyncio.Queue(maxsize=max_queue)
        self._carry: _Item | None = None
        # The batch being collected, then run: stop() fails what is left in it.
        self._inflight: list[_Item] = []
        self._task: asyncio.Task[None] | None = None

    @property
    def pending(self) -> int:
        """Number of clips waiting for a batch."""
        return self._queue.qsize() + (self._carry is not None)

    def start(self) -> None:
        """Start the batching loop."""
        self._task = asyncio.create_task(self._run(), name="asr-batcher")

    async def stop(self) -> None:
        """Stop the loop and fail every request that has not been answered."""
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        leftovers = [*self._inflight]
        if self._carry is not None:
            leftovers.append(self._carry)
            self._carry = None
        while not self._queue.empty():
            leftovers.append(self._queue.get_nowait())
        for item in leftovers:
            if not item.future.done():
                item.future.set_exception(
                    BatcherClosedError("The ASR service is shutting down")
                )

    async def submit(self, wav: np.ndarray) -> str:
        """Queue one clip and wait for its text.

        Raises:
            QueueFullError: when ``max_queue`` clips are already waiting.
            BatcherClosedError: when the batcher is not running.
        """
        if self._task is None or self._task.done():
            raise BatcherClosedError("The ASR batcher is not running")
        item = _Item(wav, asyncio.get_running_loop().create_future())
        try:
            self._queue.put_nowait(item)
        except asyncio.QueueFull:
            raise QueueFullError(
                f"Too many pending requests ({self._queue.maxsize}); retry later"
            ) from None
        return await item.future

    async def _next_batch(self) -> list[_Item]:
        loop = asyncio.get_running_loop()
        first = self._carry if self._carry is not None else await self._queue.get()
        self._carry = None
        batch = self._inflight = [first]
        longest = first.wav.shape[0]
        deadline = loop.time() + self._max_wait_s
        while len(batch) < self._max_batch_size:
            try:
                item = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    item = await asyncio.wait_for(self._queue.get(), remaining)
                except TimeoutError:
                    break
            new_longest = max(longest, item.wav.shape[0])
            if new_longest * (len(batch) + 1) > self._max_batch_samples:
                self._carry = item  # first clip of the next batch
                break
            batch.append(item)
            longest = new_longest
        return batch

    async def _run(self) -> None:
        loop = asyncio.get_running_loop()
        while True:
            batch = await self._next_batch()
            # Futures of requests whose client went away are already cancelled.
            live = sorted(
                (item for item in batch if not item.future.done()),
                key=lambda item: item.wav.shape[0],
            )
            if not live:
                self._inflight = []
                continue
            self._inflight = live
            started = time.perf_counter()
            try:
                texts = await loop.run_in_executor(
                    self._executor, self._transcribe, [item.wav for item in live]
                )
                if len(texts) != len(live):
                    raise RuntimeError(
                        f"model returned {len(texts)} texts for {len(live)} clips"
                    )
            except Exception as exc:
                self._inflight = []
                logger.exception(
                    "Transcription of a batch of %d clips failed", len(live)
                )
                for item in live:
                    if not item.future.done():
                        item.future.set_exception(exc)
                continue
            # Kept until here so that stop() can fail the in-flight requests.
            self._inflight = []
            for item, text in zip(live, texts, strict=True):
                if not item.future.done():
                    item.future.set_result(text)
            samples = [item.wav.shape[0] for item in live]
            logger.debug(
                "Transcribed a batch of %d clip(s), %.2f s of audio (longest %.2f s), "
                "in %.0f ms; %d waiting",
                len(live),
                sum(samples) / SAMPLE_RATE,
                max(samples) / SAMPLE_RATE,
                (time.perf_counter() - started) * 1000,
                self.pending,
            )
