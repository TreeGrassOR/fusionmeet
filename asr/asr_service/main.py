"""FastAPI application: ``POST /v1/transcribe`` and ``GET /health``.

Run with ``uvicorn asr_service.main:create_app --factory``. The model loads in the
background after the server starts; ``/health`` answers 503 until it is ready. If it
cannot be loaded, the error is logged and the process exits with status 1.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import __version__
from .audio import SAMPLE_RATE, AudioError, decode_audio
from .batcher import BatcherClosedError, MicroBatcher, QueueFullError
from .config import ConfigError, Settings
from .engine import MIN_SAMPLES, Engine, FatalStartupError, load_engine

logger = logging.getLogger("asr_service")

EngineLoader = Callable[[Settings], Engine]
FatalHandler = Callable[[int], None]

# Room for a WAV header and its chunks on top of the samples themselves.
_BODY_OVERHEAD_BYTES = 64 * 1024


class TranscriptionResponse(BaseModel):
    """Body of a successful ``POST /v1/transcribe``."""

    text: str
    duration: float
    model: str
    revision: str
    variant: str
    processing_ms: int


class ASRService:
    """Model lifecycle and request handlers; ``create_app`` wires them to routes."""

    def __init__(
        self, settings: Settings, loader: EngineLoader, on_fatal: FatalHandler
    ):
        """Prepare the service; the model is loaded by ``lifespan``."""
        self.settings = settings
        self.engine: Engine | None = None
        self.batcher: MicroBatcher | None = None
        self._loader = loader
        self._on_fatal = on_fatal
        self._max_body_bytes = (
            int(settings.max_audio_seconds * SAMPLE_RATE * 4) + _BODY_OVERHEAD_BYTES
        )
        # One thread loads the model and then runs every forward pass.
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="asr-model"
        )

    @contextlib.asynccontextmanager
    async def lifespan(self, _app: FastAPI) -> AsyncIterator[None]:
        """Load the model in the background while the server already answers."""
        loading = asyncio.create_task(self._load_model(), name="asr-load-model")
        try:
            yield
        finally:
            loading.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await loading
            if self.batcher is not None:
                await self.batcher.stop()
            self._executor.shutdown(wait=False, cancel_futures=True)

    async def _load_model(self) -> None:
        settings = self.settings
        loop = asyncio.get_running_loop()
        try:
            engine = await loop.run_in_executor(self._executor, self._loader, settings)
        except FatalStartupError as exc:
            logger.critical("ASR service cannot start: %s", exc)
            self._on_fatal(1)
            return
        except Exception:
            logger.critical(
                "ASR service cannot start: model loading failed", exc_info=True
            )
            self._on_fatal(1)
            return
        batcher = MicroBatcher(
            engine.transcribe_batch,
            self._executor,
            max_batch_size=settings.max_batch_size,
            max_wait_s=settings.batch_wait_ms / 1000,
            max_batch_samples=int(settings.max_batch_audio_seconds * SAMPLE_RATE),
            max_queue=settings.max_queue,
        )
        batcher.start()
        self.engine, self.batcher = engine, batcher
        info = engine.info
        logger.info(
            "ASR service ready: %s variant %s (revision %s) on %s, max batch %d",
            info.model_id,
            info.variant,
            info.revision,
            info.device,
            settings.max_batch_size,
        )

    def require_api_key(self, request: Request) -> None:
        """FastAPI dependency: 401 unless ``Authorization: Bearer <ASR_API_KEY>``."""
        scheme, _, token = request.headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not secrets.compare_digest(
            token.strip().encode(), self.settings.api_key.encode()
        ):
            raise HTTPException(
                status_code=401,
                detail="Missing or invalid API key",
                headers={"WWW-Authenticate": "Bearer"},
            )

    async def health(self) -> Any:
        """``GET /health``: 503 while loading, then the model description."""
        engine = self.engine
        if engine is None or self.batcher is None:
            return JSONResponse({"status": "loading"}, status_code=503)
        info = engine.info
        return {
            "status": "ok",
            "model": info.model_id,
            "revision": info.revision,
            "variant": info.variant,
            "device": info.device,
            "fp16_encoder": info.fp16_encoder,
            "vram_limit_gb": info.vram_limit_gb,
            "vram_free_gb_at_start": info.vram_free_gb_at_start,
            "max_batch_size": self.settings.max_batch_size,
        }

    async def transcribe(self, request: Request) -> TranscriptionResponse:
        """``POST /v1/transcribe``: decode the body and queue it for the model."""
        started = time.perf_counter()
        engine, batcher = self.engine, self.batcher
        if engine is None or batcher is None:
            raise HTTPException(
                status_code=503,
                detail="The model is still loading",
                headers={"Retry-After": "5"},
            )
        max_seconds = self.settings.max_audio_seconds
        body = await _read_body(request, self._max_body_bytes, max_seconds)
        try:
            wav = decode_audio(body, request.headers.get("content-type"))
        except AudioError as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

        duration = wav.shape[0] / SAMPLE_RATE
        if duration > max_seconds:
            raise HTTPException(
                status_code=413,
                detail=f"Audio is {duration:.2f} s long; "
                f"the limit is {max_seconds:g} s",
            )

        text = ""
        if wav.shape[0] >= MIN_SAMPLES:
            try:
                text = await _unless_disconnected(request, batcher.submit(wav))
            except ClientDisconnectedError as exc:
                # The server drops this response (and its access log line).
                raise HTTPException(
                    status_code=499, detail="Client closed the request"
                ) from exc
            except QueueFullError as exc:
                raise HTTPException(
                    status_code=503, detail=str(exc), headers={"Retry-After": "1"}
                ) from exc
            except BatcherClosedError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except Exception as exc:
                # Already logged with its traceback by the batcher.
                raise HTTPException(
                    status_code=500, detail="Transcription failed"
                ) from exc

        info = engine.info
        return TranscriptionResponse(
            text=text,
            duration=round(duration, 3),
            model=info.model_id,
            revision=info.revision,
            variant=info.variant,
            processing_ms=round((time.perf_counter() - started) * 1000),
        )


def exit_process(code: int) -> None:
    """Flush the logs and terminate the process at once (the server cannot run)."""
    logging.shutdown()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


def create_app(
    settings: Settings | None = None,
    *,
    engine_loader: EngineLoader | None = None,
    on_fatal: FatalHandler | None = None,
) -> FastAPI:
    """Build the application (uvicorn factory).

    Args:
        settings: the settings; read from the environment when omitted.
        engine_loader: loads the model (injected by tests).
        on_fatal: called with the exit status when the model cannot be loaded.
    """
    if settings is None:
        try:
            settings = Settings.from_env()
        except ConfigError as exc:
            _configure_logging("info")
            logger.critical("Invalid ASR configuration: %s", exc)
            raise SystemExit(1) from exc
    _configure_logging(settings.log_level)

    service = ASRService(
        settings,
        engine_loader if engine_loader is not None else load_engine,
        on_fatal if on_fatal is not None else exit_process,
    )
    app = FastAPI(
        title="FusionMeet ASR",
        version=__version__,
        lifespan=service.lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.asr = service
    app.add_api_route("/health", service.health, methods=["GET"])
    app.add_api_route(
        "/v1/transcribe",
        service.transcribe,
        methods=["POST"],
        response_model=TranscriptionResponse,
        dependencies=[Depends(service.require_api_key)],
    )
    return app


class ClientDisconnectedError(Exception):
    """The client went away before its clip was transcribed."""


async def _wait_disconnect(request: Request) -> None:
    """Return once the client closes the connection (after the body was read)."""
    while (await request.receive())["type"] != "http.disconnect":
        pass


async def _unless_disconnected(request: Request, submission: Awaitable[str]) -> str:
    """Await ``submission``, cancelling it if the client disconnects first.

    Servers do not cancel the handler of a client that went away (an agent that
    timed out and retries, for example). Cancelling the submission cancels the
    future of its queued clip, which the batcher then skips instead of running
    the model for nobody.

    Raises:
        ClientDisconnectedError: the client went away first.
    """
    work = asyncio.ensure_future(submission)
    watcher = asyncio.ensure_future(_wait_disconnect(request))
    try:
        done, _ = await asyncio.wait(
            {work, watcher}, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        watcher.cancel()
        # cancels the clip's future at once (the task is waiting on it)
        work.cancel()
    if work not in done:
        raise ClientDisconnectedError
    return work.result()


async def _read_body(request: Request, limit: int, max_seconds: float) -> bytes:
    """Read the request body, refusing with 413 anything larger than ``limit`` bytes."""
    too_large = HTTPException(
        status_code=413,
        detail=f"Request body is larger than {limit} bytes "
        f"(more than {max_seconds:g} s of 16 kHz audio)",
    )
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise too_large
    chunks = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise too_large
        chunks.append(chunk)
    return b"".join(chunks)


def _configure_logging(level: str) -> None:
    """Send the service logs to stderr unless logging is already configured."""
    package_logger = logging.getLogger("asr_service")
    package_logger.setLevel(logging.DEBUG if level == "trace" else level.upper())
    if not logging.getLogger().handlers and not package_logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")
        )
        package_logger.addHandler(handler)
