"""Bring the transcriber back to the transcriptions that lost it.

LiveKit (v1.13.6) does not dispatch a job again when its process dies (crash,
container killed), although the dispatch has the ``JRP_ON_FAILURE`` restart
policy. The phrases already written to S3 survive, but nobody would resume the
transcription nor finalize it on stop.

The transcriber *worker* process (never a job process) therefore checks the
rooms every ``TRANSCRIPTION_RECONCILE_INTERVAL_S`` seconds (default 20) and
once right after the worker registered with LiveKit: a room whose metadata
holds a transcription signed by the backend (``transcription_control``), with
status ``started`` or ``stopping``, and where the transcriber participant is
absent, gets a new dispatch of the transcriber. The new job resumes the
transcript from its S3 parts, and finalizes it at once when it is
``stopping``. Duplicates are rejected by the job request handler of the
transcriber (one transcriber per room).
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from livekit import api, rtc

from transcript_store import StoreConfig
from transcription_control import STATUS_STARTED, parse_room_metadata

if TYPE_CHECKING:
    from livekit.agents import AgentServer

logger = logging.getLogger("transcription-reconciler")

DEFAULT_INTERVAL_S = 20.0
MIN_INTERVAL_S = 5.0
# the backend dispatches the transcriber right after writing a "started" entry:
# leave that job the time to join before considering it missing
DISPATCH_GRACE_S = 15.0


class TranscriptionReconciler:
    """Re-dispatch the transcriber to the rooms whose transcription lost it."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        agent_name: str,
        token: str,
        interval_s: float = DEFAULT_INTERVAL_S,
        api_factory: Callable[[], api.LiveKitAPI] = api.LiveKitAPI,
        busy_rooms: Callable[[], Iterable[str]] = tuple,
        paused: Callable[[], bool] = lambda: False,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Init the reconciler.

        Args:
            agent_name: Name of the transcriber agent (TRANSCRIBER_AGENT_NAME).
            token: TRANSCRIPTION_AGENT_TOKEN, the key of the metadata signature.
            interval_s: Period of the checks, in seconds.
            api_factory: Builds a LiveKit API client (async context manager);
                by default from LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET.
            busy_rooms: Rooms where this worker already runs a job (its
                participant may not have joined yet).
            paused: Whether to skip the checks (the worker is draining).
            clock: Wall-clock source in epoch seconds (tests).
        """
        self._agent_name = agent_name
        self._token = token
        self.interval_s = interval_s
        self._api_factory = api_factory
        self._busy_rooms = busy_rooms
        self._paused = paused
        self._clock = clock
        self._task: asyncio.Task | None = None

    @classmethod
    def from_env(cls, agent_name: str) -> TranscriptionReconciler | None:
        """Build the reconciler; None when the transcript storage is disabled.

        The storage needs AWS_STORAGE_BUCKET_NAME and TRANSCRIPTION_HOOK_URL,
        and TRANSCRIPTION_AGENT_TOKEN to verify the metadata. A
        TRANSCRIPTION_RECONCILE_INTERVAL_S of 0 disables the reconciliation.
        """
        config = StoreConfig.from_env()
        interval_s = float(
            os.getenv("TRANSCRIPTION_RECONCILE_INTERVAL_S", str(DEFAULT_INTERVAL_S))
        )
        if config is None or not config.hook_token or interval_s <= 0:
            return None
        return cls(
            agent_name=agent_name,
            token=config.hook_token,
            interval_s=max(MIN_INTERVAL_S, interval_s),
        )

    def transcriber_identity(self, room_name: str) -> str:
        """Identity of the transcriber in a room (see the job request handler)."""
        return f"{self._agent_name}-{room_name}"

    def attach(self, server: AgentServer) -> None:
        """Run the checks in the event loop of a worker (``AgentServer``).

        The loop starts when the worker is registered with LiveKit (a dispatch
        without any registered worker would be lost), once; it is cancelled
        with the other tasks of the worker loop when the worker exits. The
        checks are skipped while the worker drains. Job processes never run it:
        they do not start the worker.
        """
        self._busy_rooms = lambda: {info.job.room.name for info in server.active_jobs}
        self._paused = lambda: server.draining

        def on_registered(*_args) -> None:
            # the worker re-raises a TypeError of an event handler: never raise
            try:
                if self._task is None:
                    self._task = asyncio.create_task(
                        self.run(), name="transcription_reconciler"
                    )
            except Exception:
                logger.exception("could not start the transcription reconciliation")

        server.on("worker_registered", on_registered)

    async def run(self) -> None:
        """Check the rooms now, then every ``interval_s`` seconds (never raises)."""
        logger.info(
            "checking every %.0fs that each transcription has its transcriber",
            self.interval_s,
        )
        while True:
            try:
                if not self._paused():
                    await self.reconcile_once()
            except Exception:
                logger.exception("transcription reconciliation failed")
            await asyncio.sleep(self.interval_s)

    async def reconcile_once(self) -> list[str]:
        """Check every room once.

        Returns:
            The rooms the transcriber was dispatched to.
        """
        dispatched: list[str] = []
        try:
            async with self._api_factory() as lkapi:
                response = await lkapi.room.list_rooms(api.ListRoomsRequest())
                busy = set(self._busy_rooms())
                for room in response.rooms:
                    if await self._reconcile_room(lkapi, room, busy):
                        dispatched.append(room.name)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("could not check the transcriptions of the rooms: %r", exc)
        return dispatched

    async def _reconcile_room(self, lkapi, room, busy: set[str]) -> bool:
        request = parse_room_metadata(
            room.metadata, room_id=room.name, token=self._token
        )
        if request is None or room.name in busy:
            return False
        if (
            request.status == STATUS_STARTED
            and request.started_at_ms is not None
            and self._clock() * 1000 - request.started_at_ms < DISPATCH_GRACE_S * 1000
        ):
            return False
        identity = self.transcriber_identity(room.name)
        try:
            participants = await lkapi.room.list_participants(
                api.ListParticipantsRequest(room=room.name)
            )
            if any(
                p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT
                and p.identity == identity
                for p in participants.participants
            ):
                return False
            dispatch = await lkapi.agent_dispatch.create_dispatch(
                api.CreateAgentDispatchRequest(
                    agent_name=self._agent_name, room=room.name
                )
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning(
                "could not dispatch the transcriber to room %s: %r", room.name, exc
            )
            return False
        logger.info(
            "transcription %s (%s) had no transcriber in room %s: dispatched it (%s)",
            request.id,
            request.status,
            room.name,
            dispatch.id,
        )
        return True
