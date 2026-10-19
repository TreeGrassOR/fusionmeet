"""Start and stop the stored transcription from the LiveKit room metadata.

The backend writes the room metadata key ``transcription``::

    {"id": "<Transcript uuid>", "status": "started" | "stopping",
     "room_name": "...", "started_at": "...Z", "started_by": "...",
     "sig": "<hex HMAC-SHA256 of '<room name>:<id>', key TRANSCRIPTION_AGENT_TOKEN>"}

and removes it once the transcript is ready. The controller opens a
``TranscriptStore`` when a new id appears with status ``started`` and
finalizes it when its status turns to ``stopping``, when the key disappears or
is replaced by another id, and when the call ends (``aclose``).

Room owners and administrators can write the room metadata themselves (LiveKit
roomAdmin grant): an entry whose signature does not match the room the agent is
in is ignored like an absent entry, so a room can never make the agent write to
the transcript of another room.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import re
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from transcript_store import (
    AgentHook,
    StoreConfig,
    TranscriptStore,
    WaitPending,
    create_s3_client,
    format_timestamp,
    parse_timestamp,
)

logger = logging.getLogger("transcription-control")

METADATA_KEY = "transcription"
STATUS_STARTED = "started"
STATUS_STOPPING = "stopping"
# the id becomes an S3 key segment: backend ids are UUIDs, refuse anything that
# could escape the transcript prefix ("/", "..")
TRANSCRIPT_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


@dataclass(frozen=True)
class TranscriptionRequest:
    """The ``transcription`` entry of the room metadata."""

    id: str
    status: str
    room_name: str = ""
    started_at_ms: int | None = None
    started_by: str = ""


def sign_transcription(room_id: str, transcript_id: str, token: str) -> str:
    """Signature of a transcription entry, as computed by the backend.

    Args:
        room_id: LiveKit room name (the backend Room id).
        transcript_id: Backend Transcript id.
        token: TRANSCRIPTION_AGENT_TOKEN shared with the backend.

    Returns:
        The hex HMAC-SHA256 of ``<room_id>:<transcript_id>``.
    """
    return hmac.new(
        token.encode(), f"{room_id}:{transcript_id}".encode(), hashlib.sha256
    ).hexdigest()


def has_valid_signature(
    entry: dict, transcript_id: str, *, room_id: str, token: str
) -> bool:
    """Whether the entry was signed by the backend for this room."""
    sig = entry.get("sig")
    if not token or not isinstance(sig, str):
        return False
    expected = sign_transcription(room_id, transcript_id, token)
    return hmac.compare_digest(expected.encode(), sig.encode())


def parse_room_metadata(
    raw: str | None, *, room_id: str, token: str
) -> TranscriptionRequest | None:
    """Extract the running transcription from the room metadata, if any.

    Args:
        raw: Room metadata (JSON).
        room_id: LiveKit room name the agent is in.
        token: TRANSCRIPTION_AGENT_TOKEN, the key of the entry signature.

    Returns:
        The transcription request, None when there is none or when the entry
        is invalid or not signed for this room.
    """
    if not raw:
        return None
    try:
        metadata = json.loads(raw)
    except ValueError:
        logger.warning("room metadata is not JSON, ignoring it")
        return None
    entry = metadata.get(METADATA_KEY) if isinstance(metadata, dict) else None
    if not isinstance(entry, dict):
        return None
    transcript_id = entry.get("id")
    status = entry.get("status")
    if (
        not isinstance(transcript_id, str)
        or not TRANSCRIPT_ID_PATTERN.fullmatch(transcript_id)
        or status not in (STATUS_STARTED, STATUS_STOPPING)
    ):
        logger.warning("ignoring invalid transcription metadata: %r", entry)
        return None
    if not has_valid_signature(entry, transcript_id, room_id=room_id, token=token):
        logger.warning(
            "ignoring transcription %s: missing or invalid signature for room %s%s",
            transcript_id,
            room_id,
            "" if token else " (TRANSCRIPTION_AGENT_TOKEN is not set)",
        )
        return None
    started_at_ms = None
    if isinstance(started_at := entry.get("started_at"), str):
        try:
            started_at_ms = parse_timestamp(started_at)
        except ValueError:
            logger.warning("invalid transcription started_at: %r", started_at)
    return TranscriptionRequest(
        id=transcript_id,
        status=status,
        room_name=str(entry.get("room_name") or ""),
        started_at_ms=started_at_ms,
        started_by=str(entry.get("started_by") or ""),
    )


class TranscriptionController:
    """Route final phrases to the transcription(s) of the room."""

    def __init__(
        self,
        *,
        room_id: str,
        config: StoreConfig,
        wait_pending: WaitPending | None = None,
        store_factory: Callable[[TranscriptionRequest], TranscriptStore] | None = None,
        hook: AgentHook | None = None,
    ) -> None:
        """Init the controller.

        Args:
            room_id: LiveKit room name (the backend Room id).
            config: Storage settings.
            wait_pending: Waits for the phrases still being recognized before
                a store is finalized (see ``gigaam_stt.wait_idle``).
            store_factory: Builds a store for a request (tests); by default an
                S3-backed store notifying the backend hook.
            hook: Backend notification (tests); by default from the settings.
        """
        self._room_id = room_id
        self._config = config
        self._wait_pending = wait_pending
        self._store_factory = store_factory or self._default_store
        self._s3 = None
        self._hook = hook or AgentHook(config.hook_url, config.hook_token)
        self._current: TranscriptStore | None = None
        # stores being finalized: they still take phrases started before the stop
        self._finalizing: dict[str, TranscriptStore] = {}
        self._handled_ids: set[str] = set()
        self._lock = asyncio.Lock()
        self._tasks: set[asyncio.Task] = set()
        self._closed = False

    @property
    def current(self) -> TranscriptStore | None:
        """The open store, if a transcription is running."""
        return self._current

    def _default_store(self, request: TranscriptionRequest) -> TranscriptStore:
        if self._s3 is None:
            self._s3 = create_s3_client()
        return TranscriptStore(
            transcript_id=request.id,
            room_id=self._room_id,
            room_name=request.room_name,
            started_at_ms=request.started_at_ms,
            config=self._config,
            s3_client=self._s3,
            hook=self._hook,
        )

    def _spawn(self, coro: Coroutine[Any, Any, Any], description: str) -> None:
        task = asyncio.create_task(coro)
        self._tasks.add(task)

        def _done(t: asyncio.Task) -> None:
            self._tasks.discard(t)
            if not t.cancelled() and (exc := t.exception()) is not None:
                logger.error("failed to %s", description, exc_info=exc)

        task.add_done_callback(_done)

    def on_room_metadata_changed(self, _old_metadata: str, new_metadata: str) -> None:
        """Handle the rtc.Room ``room_metadata_changed`` event."""
        self._spawn(self.apply_metadata(new_metadata), "apply the room metadata")

    async def apply_metadata(self, raw: str | None) -> None:
        """Open or finalize stores according to the room metadata."""
        request = parse_room_metadata(
            raw, room_id=self._room_id, token=self._config.hook_token
        )
        async with self._lock:
            if self._closed:
                return
            current = self._current
            if current is not None and (
                request is None
                or request.id != current.transcript_id
                or request.status == STATUS_STOPPING
            ):
                self._current = None
                self._start_finalize(current)
            if (
                request is None
                or request.id in self._handled_ids
                or self._current is not None
            ):
                return
            self._handled_ids.add(request.id)
            try:
                store = await asyncio.to_thread(self._store_factory, request)
                await store.open()
            except Exception as exc:
                logger.exception("could not open transcript %s", request.id)
                await self._report_failure(request, exc)
                return
            logger.info("transcription %s %s", request.id, request.status)
            if request.status == STATUS_STOPPING:
                self._start_finalize(store)
            else:
                self._current = store

    async def _report_failure(
        self, request: TranscriptionRequest, exc: Exception
    ) -> None:
        now_ms = round(time.time() * 1000)
        await self._hook.notify(
            {
                "transcript_id": request.id,
                "room_id": self._room_id,
                "status": "failed",
                "started_at": format_timestamp(request.started_at_ms or now_ms),
                "ended_at": format_timestamp(now_ms),
                "participants": [],
                "segments_count": 0,
                "files": {"json": None, "md": None},
                "error": f"{type(exc).__name__}: {exc}",
            }
        )

    def _start_finalize(self, store: TranscriptStore) -> None:
        self._finalizing[store.transcript_id] = store
        self._spawn(self._finalize(store), f"finalize {store.transcript_id}")

    async def _finalize(self, store: TranscriptStore) -> None:
        try:
            await store.finalize(wait_pending=self._wait_pending)
        finally:
            self._finalizing.pop(store.transcript_id, None)

    def add_final(
        self, *, identity: str, name: str, start: float, end: float, text: str
    ) -> None:
        """Add a final phrase (epoch seconds) to the stores that accept it."""
        stores = list(self._finalizing.values())
        if self._current is not None:
            stores.append(self._current)
        for store in stores:
            store.add_segment(
                identity=identity, name=name, start=start, end=end, text=text
            )

    async def aclose(self) -> None:
        """Finalize the running transcription (the call ended)."""
        async with self._lock:
            self._closed = True
            if (current := self._current) is not None:
                self._current = None
                self._start_finalize(current)
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
