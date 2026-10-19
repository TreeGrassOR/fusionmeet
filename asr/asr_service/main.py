"""FastAPI application: ``POST /v1/transcribe``, ``GET /v1/models``, ``GET /health``.

Run with ``uvicorn asr_service.main:create_app --factory``. Every enabled model loads
in the background after the server starts, the default model first; ``/health``
answers 503 until the default model is ready. A model that cannot be loaded is
reported as ``failed`` and the others keep serving.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import time
from collections.abc import AsyncIterator, Awaitable
from typing import Any, TypeVar

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from . import __version__
from .audio import SAMPLE_RATE, AudioError, decode_audio
from .batcher import BatcherClosedError, QueueFullError
from .config import ConfigError, Settings
from .engine import MIN_SAMPLES
from .models import (
    LOADING,
    Backends,
    ModelManager,
    ModelUnavailableError,
    RealBackends,
)
from .registry import MODELS, ModelSpec

logger = logging.getLogger("asr_service")

# Room for a WAV header and its chunks on top of the samples themselves.
_BODY_OVERHEAD_BYTES = 64 * 1024

T = TypeVar("T")


class TranscriptionResponse(BaseModel):
    """Body of a successful ``POST /v1/transcribe``."""

    text: str
    duration: float
    model_id: str
    model_label: str
    model: str
    revision: str
    variant: str
    processing_ms: int


class ASRService:
    """Model lifecycle and request handlers; ``create_app`` wires them to routes."""

    def __init__(self, settings: Settings, backends: Backends):
        """Prepare the service; the models are loaded by ``lifespan``."""
        self.settings = settings
        self.manager = ModelManager(settings, backends)
        self._max_body_bytes = (
            int(settings.max_audio_seconds * SAMPLE_RATE * 4) + _BODY_OVERHEAD_BYTES
        )

    @contextlib.asynccontextmanager
    async def lifespan(self, _app: FastAPI) -> AsyncIterator[None]:
        """Load the models in the background while the server already answers."""
        loading = asyncio.create_task(self.manager.load(), name="asr-load-models")
        try:
            yield
        finally:
            loading.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await loading
            await self.manager.stop()

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
        """``GET /health``: 503 until the default model is ready, then 200."""
        body = self.manager.health()
        return JSONResponse(body, status_code=200 if body["status"] == "ok" else 503)

    async def list_models(self) -> dict[str, Any]:
        """``GET /v1/models``: the enabled models with their status."""
        return self.manager.describe()

    def _resolve_model(self, model_id: str | None) -> ModelSpec:
        """Return the requested model (the default when none is given), or a 400."""
        model_id = (model_id or "").strip().lower() or self.settings.default_model
        enabled = ", ".join(self.manager.models)
        if model_id not in MODELS:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown model {model_id!r}; enabled models: {enabled}",
            )
        if model_id not in self.manager.models:
            raise HTTPException(
                status_code=400,
                detail=f"Model {model_id!r} is not enabled on this server; "
                f"enabled models: {enabled}",
            )
        return self.manager.models[model_id]

    async def transcribe(
        self, request: Request, model: str | None = None
    ) -> TranscriptionResponse:
        """``POST /v1/transcribe?model=<id>``: decode the body and queue it."""
        started = time.perf_counter()
        spec = self._resolve_model(model)
        try:
            self.manager.check_available(spec)
        except ModelUnavailableError as exc:
            raise _unavailable(exc) from exc
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
        info = self.manager.engine_info(spec)
        if wav.shape[0] >= MIN_SAMPLES:
            try:
                text, info = await _unless_disconnected(
                    request, self.manager.transcribe(spec, wav)
                )
            except ClientDisconnectedError as exc:
                # The server drops this response (and its access log line).
                raise HTTPException(
                    status_code=499, detail="Client closed the request"
                ) from exc
            except ModelUnavailableError as exc:
                raise _unavailable(exc) from exc
            except QueueFullError as exc:
                raise HTTPException(
                    status_code=503, detail=str(exc), headers={"Retry-After": "1"}
                ) from exc
            except BatcherClosedError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            except Exception as exc:
                # Already logged with its traceback by the batcher or the manager.
                raise HTTPException(
                    status_code=500, detail="Transcription failed"
                ) from exc

        return TranscriptionResponse(
            text=text,
            duration=round(duration, 3),
            model_id=spec.id,
            model_label=spec.label,
            model="" if info is None else info.model_id,
            revision="" if info is None else info.revision,
            variant="" if info is None else info.variant,
            processing_ms=round((time.perf_counter() - started) * 1000),
        )


def _unavailable(exc: ModelUnavailableError) -> HTTPException:
    """503 for a model that is loading (with Retry-After) or failed."""
    headers = {"Retry-After": "5"} if exc.status == LOADING else None
    return HTTPException(status_code=503, detail=str(exc), headers=headers)


def create_app(
    settings: Settings | None = None, *, backends: Backends | None = None
) -> FastAPI:
    """Build the application (uvicorn factory).

    Args:
        settings: the settings; read from the environment when omitted.
        backends: places and loads the models (injected by tests).
    """
    if settings is None:
        try:
            settings = Settings.from_env()
        except ConfigError as exc:
            _configure_logging("info")
            logger.critical("Invalid ASR configuration: %s", exc)
            raise SystemExit(1) from exc
    _configure_logging(settings.log_level)
    if settings.ignored_variables:
        logger.warning(
            "Ignoring %s: the models and their revisions now come from ASR_MODELS "
            "and the model registry (see asr/README.md)",
            ", ".join(settings.ignored_variables),
        )
    logger.info(
        "Models: %s (default %s), ASR_DEVICE=%s, %d CPU thread(s) per model",
        ", ".join(settings.models),
        settings.default_model,
        settings.device,
        settings.cpu_threads,
    )

    service = ASRService(settings, backends if backends is not None else RealBackends())
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
        "/v1/models",
        service.list_models,
        methods=["GET"],
        dependencies=[Depends(service.require_api_key)],
    )
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


async def _unless_disconnected(request: Request, submission: Awaitable[T]) -> T:
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
