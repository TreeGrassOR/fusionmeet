"""Recognition model of the room, read from the backend.

The room owner picks the recognition model in the transcription settings of
the room. The agent reads it from the backend::

    GET <TRANSCRIPTION_SETTINGS_URL>?room=<LiveKit room name>
    Authorization: Bearer <TRANSCRIPTION_AGENT_TOKEN>
    -> {"asr_model": "<model id>" | null}

The value is cached for ``TRANSCRIPTION_SETTINGS_TTL_S`` seconds (15 by
default), so a change reaches the phrases recognized after at most that delay.
When the backend cannot be read the last known value is kept (none at the
start of the job: the ASR service then uses its default model) and the next
read happens after the same delay. ``None`` means the default model of the
ASR service.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable

import httpx

logger = logging.getLogger("transcription-settings")

DEFAULT_TTL_S = 15.0
REQUEST_TIMEOUT_S = 5.0
MAX_MODEL_ID_LENGTH = 64  # RoomTranscriptionSettings.asr_model in the backend


class RoomSettings:
    """Per-job cache of the recognition model chosen for the room."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        url: str,
        token: str,
        room: str,
        ttl_s: float = DEFAULT_TTL_S,
        timeout_s: float = REQUEST_TIMEOUT_S,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Configure the cache.

        Args:
            url: URL of the backend agent-settings endpoint.
            token: Bearer token shared with the backend.
            room: LiveKit room name (the backend Room id).
            ttl_s: How long a read value is used before reading it again.
            timeout_s: Timeout of one read.
            transport: Optional HTTP transport (tests).
            clock: Monotonic time source (tests).
        """
        self.url = url
        self.room = room
        self.ttl_s = max(0.0, ttl_s)
        self._token = token
        self._timeout_s = timeout_s
        self._transport = transport
        self._clock = clock
        self._client: httpx.AsyncClient | None = None
        self._lock = asyncio.Lock()
        self._asr_model: str | None = None
        self._expires_at: float | None = None

    @classmethod
    def from_env(cls, room: str) -> RoomSettings | None:
        """Read the settings; None when the room settings are not configured."""
        url = os.getenv("TRANSCRIPTION_SETTINGS_URL", "")
        if not url:
            return None
        token = os.getenv("TRANSCRIPTION_AGENT_TOKEN", "")
        if not token:
            logger.warning(
                "TRANSCRIPTION_SETTINGS_URL is set but TRANSCRIPTION_AGENT_TOKEN "
                "is not: the ASR default model is used"
            )
            return None
        return cls(
            url=url,
            token=token,
            room=room,
            ttl_s=float(os.getenv("TRANSCRIPTION_SETTINGS_TTL_S", str(DEFAULT_TTL_S))),
        )

    def _fresh(self) -> bool:
        return self._expires_at is not None and self._clock() < self._expires_at

    async def asr_model(self) -> str | None:
        """The model id chosen for the room, None for the ASR default model."""
        if self._fresh():
            return self._asr_model
        async with self._lock:
            # concurrent phrases wait for one read
            if not self._fresh():
                await self._refresh()
                self._expires_at = self._clock() + self.ttl_s
        return self._asr_model

    async def _refresh(self) -> None:
        try:
            # phrases wait for this read: bound it as a whole (the httpx
            # timeout applies to each phase separately: connect, write, read)
            async with asyncio.timeout(self._timeout_s):
                value = await self._read()
        except Exception as exc:  # the settings never stop the transcription
            logger.warning(
                "could not read the transcription settings of room %s, "
                "keeping the recognition model %s: %r",
                self.room,
                self._asr_model or "(ASR default)",
                exc,
            )
            return
        if value != self._asr_model:
            logger.info(
                "recognition model of room %s: %s",
                self.room,
                value or "(ASR default)",
            )
        self._asr_model = value

    async def _read(self) -> str | None:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                transport=self._transport, timeout=self._timeout_s
            )
        response = await self._client.get(
            self.url,
            params={"room": self.room},
            headers={"Authorization": f"Bearer {self._token}"},
        )
        if response.status_code != httpx.codes.OK:
            raise ValueError(f"HTTP {response.status_code}: {response.text[:200]!r}")
        payload = response.json()
        if isinstance(payload, dict) and "asr_model" in payload:
            value = payload["asr_model"]
            if value is None or (
                isinstance(value, str) and 0 < len(value) <= MAX_MODEL_ID_LENGTH
            ):
                return value
        raise ValueError(f"unexpected response: {response.text[:200]!r}")

    async def aclose(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
