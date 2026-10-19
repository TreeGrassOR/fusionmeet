"""Multi user transcription agent."""

import asyncio
import contextlib
import functools
import logging
import os
import time
from collections.abc import Callable

from dotenv import load_dotenv
from lasuite.plugins import kyutai
from livekit import api, rtc
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    AutoSubscribe,
    JobContext,
    JobProcess,
    JobRequest,
    RoomIO,
    WorkerOptions,
    WorkerPermissions,
    cli,
    utils,
)
from livekit.agents import (
    room_io as lk_room_io,
)
from livekit.agents import (
    stt as lk_stt,
)
from livekit.plugins import deepgram, silero

import gigaam_stt
import voxtral_vllm_stt
from observability import configure_sentry, set_job_context
from tasks import done_callback
from transcript_store import StoreConfig
from transcription_control import TranscriptionController
from transcription_reconciler import TranscriptionReconciler
from transcription_settings import RoomSettings

load_dotenv()

logger = logging.getLogger("transcriber")

TRANSCRIBER_AGENT_NAME = os.getenv("TRANSCRIBER_AGENT_NAME", "multi-user-transcriber")
STT_PROVIDER = os.getenv("STT_PROVIDER", "deepgram")
ENABLE_SILERO_VAD = os.getenv("ENABLE_SILERO_VAD", "true").lower() == "true"

SESSION_DRAIN_TIMEOUT_S = 15.0
# When a participant leaves, AgentSession waits session_close_transcript_timeout
# (2 s by default) for the final transcript. GigaAM recognizes the last phrase
# only once the VAD ended it, through the asr service: wait longer (keep it
# below SESSION_DRAIN_TIMEOUT_S).
GIGAAM_SESSION_CLOSE_TIMEOUT_S = float(
    os.getenv("GIGAAM_SESSION_CLOSE_TIMEOUT_S", "10")
)


def create_stt_provider(
    vad: silero.VAD | None = None,
    model_resolver: gigaam_stt.ModelResolver | None = None,
):
    """Create STT provider based on environment configuration.

    Args:
        vad: Shared, prewarmed VAD instance. Required in practice for
            voxtral-vllm (no server-side endpointing): if omitted, the plugin
            loads its own Silero model synchronously on the event loop, once
            per participant, freezing all active sessions for the duration.
        model_resolver: Recognition model chosen for the room (gigaam only).
    """
    if STT_PROVIDER == "deepgram":
        # Note: Not all Deepgram API parameters are supported by the LiveKit plugin
        # detect_language is NOT supported for real-time streaming
        # Use language="multi" instead for automatic multilingual support
        _stt_instance = deepgram.STT(
            model=os.getenv("DEEPGRAM_STT_MODEL", "nova-3"),
            language=os.getenv("DEEPGRAM_STT_LANGUAGE", "multi"),
        )
    elif STT_PROVIDER == "kyutai":
        _stt_instance = kyutai.STT(base_url=os.getenv("KYUTAI_STT_BASE_URL"))
    elif STT_PROVIDER == "voxtral-vllm":
        # The plugin resolves base_url / model / api_key from the environment.
        _stt_instance = voxtral_vllm_stt.STT(vad=vad)
    elif STT_PROVIDER == "gigaam":
        # The plugin resolves the ASR service settings from the environment.
        if vad is None:
            raise ValueError("STT_PROVIDER=gigaam requires ENABLE_SILERO_VAD=true")
        _stt_instance = gigaam_stt.STT(vad=vad, model_resolver=model_resolver)
    else:
        raise ValueError(f"Unknown STT_PROVIDER: {STT_PROVIDER}")

    return _stt_instance


class Transcriber(Agent):
    """Create a transcription agent for a specific participant."""

    def __init__(
        self,
        *,
        participant_identity: str,
        vad: silero.VAD | None = None,
        on_final: Callable[..., None] | None = None,
        model_resolver: gigaam_stt.ModelResolver | None = None,
    ):
        """Init transcription agent.

        Args:
            participant_identity: Identity of the transcribed participant.
            vad: Shared, prewarmed VAD instance.
            on_final: Called with (text, wall-clock start, wall-clock end in
                epoch seconds) and the keywords ``model`` / ``model_label``
                (recognition model, if known) for every final transcript.
            model_resolver: Recognition model chosen for the room.
        """
        stt = create_stt_provider(vad=vad, model_resolver=model_resolver)

        super().__init__(
            instructions="not-needed",
            stt=stt,
        )
        self.participant_identity = participant_identity
        self._on_final = on_final

    async def stt_node(self, audio, model_settings):
        """Forward the STT events unchanged, reporting the final transcripts."""
        async for ev in Agent.default.stt_node(self, audio, model_settings):
            if (
                self._on_final is not None
                and ev.type == lk_stt.SpeechEventType.FINAL_TRANSCRIPT
            ):
                self._report_final(ev)
            yield ev

    def _report_final(self, ev: lk_stt.SpeechEvent) -> None:
        # never let storage break the STT pipeline (and the live subtitles)
        try:
            if not ev.alternatives or not (text := ev.alternatives[0].text.strip()):
                return
            alternative = ev.alternatives[0]
            metadata = alternative.metadata or {}
            end = float(metadata.get("wall_end", time.time()))
            duration = max(0.0, alternative.end_time - alternative.start_time)
            start = float(metadata.get("wall_start", end - duration))
            self._on_final(
                text,
                start,
                end,
                model=metadata.get("model_id"),
                model_label=metadata.get("model_label"),
            )
        except Exception:
            logger.exception(
                "failed to record a final transcript of %s", self.participant_identity
            )


class MultiUserTranscriber:
    """Manage transcription sessions for multiple room participants."""

    def __init__(self, ctx: JobContext):
        """Init multi user transcription agent."""
        self.ctx = ctx
        self._sessions: dict[str, AgentSession] = {}
        self._starting: dict[str, asyncio.Task] = {}
        self._tasks: set[asyncio.Task] = set()
        self._transcription: TranscriptionController | None = None
        if (config := StoreConfig.from_env()) is not None:
            self._transcription = TranscriptionController(
                room_id=ctx.job.room.name,
                config=config,
                wait_pending=gigaam_stt.wait_idle if STT_PROVIDER == "gigaam" else None,
            )
        # recognition model chosen for the room, read from the backend
        self._room_settings = (
            RoomSettings.from_env(ctx.job.room.name)
            if STT_PROVIDER == "gigaam"
            else None
        )

    def start(self):
        """Start listening for participant connection events."""
        self.ctx.room.on("participant_connected", self.on_participant_connected)
        self.ctx.room.on("participant_disconnected", self.on_participant_disconnected)
        if self._transcription is not None:
            self.ctx.room.on(
                "room_metadata_changed", self._transcription.on_room_metadata_changed
            )

    def watch_transcription(self):
        """Apply the room metadata read on connection (stored transcription)."""
        if self._transcription is not None:
            self._transcription.on_room_metadata_changed("", self.ctx.room.metadata)

    async def aclose(self):
        """Close all sessions and cleanup resources."""
        try:
            await utils.aio.cancel_and_wait(*self._tasks)

            results = await asyncio.gather(
                *[self._close_session(session) for session in self._sessions.values()],
                return_exceptions=True,
            )
            for result in results:
                if isinstance(result, BaseException):
                    logger.warning("failed to close a session: %r", result)

            self.ctx.room.off("participant_connected", self.on_participant_connected)
            self.ctx.room.off(
                "participant_disconnected", self.on_participant_disconnected
            )
        finally:
            # the call ended: write the transcript of a running transcription
            if self._transcription is not None:
                self.ctx.room.off(
                    "room_metadata_changed",
                    self._transcription.on_room_metadata_changed,
                )
                await self._transcription.aclose()
            if self._room_settings is not None:
                await self._room_settings.aclose()

    def _on_final_transcript(  # noqa: PLR0913
        self,
        participant: rtc.RemoteParticipant,
        text: str,
        start: float,
        end: float,
        *,
        model: str | None = None,
        model_label: str | None = None,
    ) -> None:
        if self._transcription is not None:
            self._transcription.add_final(
                identity=participant.identity,
                # the name at that moment (participant objects are kept updated)
                name=participant.name or participant.identity,
                start=start,
                end=end,
                text=text,
                model=model,
                model_label=model_label,
            )

    def on_participant_connected(self, participant: rtc.RemoteParticipant):
        """Handle new participant connection by starting transcription session."""
        identity = participant.identity
        if identity in self._sessions or identity in self._starting:
            return

        logger.info(f"starting session for {identity}")
        task = asyncio.create_task(self._start_session(participant))
        self._starting[identity] = task
        self._tasks.add(task)
        task.add_done_callback(lambda t, i=identity: self._starting.pop(i, None))
        task.add_done_callback(
            done_callback(
                logger,
                self._tasks,
                f"start transcription session for {identity}",
            )
        )

    def on_participant_disconnected(self, participant: rtc.RemoteParticipant):
        """Handle participant disconnection by closing transcription session."""
        if (start_task := self._starting.pop(participant.identity, None)) is not None:
            logger.info(f"cancelling pending session start for {participant.identity}")
            start_task.cancel()
            return

        if (session := self._sessions.pop(participant.identity, None)) is None:
            return

        logger.info(f"closing session for {participant.identity}")
        task = asyncio.create_task(self._close_session(session))
        self._tasks.add(task)
        task.add_done_callback(
            done_callback(
                logger,
                self._tasks,
                f"close transcription session for {participant.identity}",
            )
        )

    async def _start_session(self, participant: rtc.RemoteParticipant) -> AgentSession:
        """Create and start transcription session for participant.

        Deduplication happens synchronously in on_participant_connected via
        self._starting; by the time this coroutine runs, the identity is
        already reserved.
        """
        vad = self.ctx.proc.userdata.get("vad", None)
        session_options = {}
        audio_input = {}
        if STT_PROVIDER == "gigaam":
            session_options["session_close_transcript_timeout"] = (
                GIGAAM_SESSION_CLOSE_TIMEOUT_S
            )
            # GigaAM works at 16 kHz; the pre-connect audio handler is per room
            # and must not be registered once per participant.
            audio_input["audio_input"] = lk_room_io.AudioInputOptions(
                sample_rate=gigaam_stt.SAMPLE_RATE, pre_connect_audio=False
            )
        session = AgentSession(vad=vad, **session_options)
        room_io = RoomIO(
            agent_session=session,
            room=self.ctx.room,
            participant=participant,
            options=lk_room_io.RoomOptions(
                text_input=False, audio_output=False, text_output=True, **audio_input
            ),
        )
        try:
            await room_io.start()
            await session.start(
                agent=Transcriber(
                    participant_identity=participant.identity,
                    vad=vad,
                    on_final=functools.partial(self._on_final_transcript, participant),
                    model_resolver=(
                        self._room_settings.asr_model if self._room_settings else None
                    ),
                )
            )
        except BaseException:
            with contextlib.suppress(Exception):
                await session.aclose()
            raise
        self._sessions[participant.identity] = session
        return session

    async def _close_session(self, sess: AgentSession) -> None:
        """Close and cleanup transcription session."""
        try:
            await asyncio.wait_for(sess.drain(), timeout=SESSION_DRAIN_TIMEOUT_S)
        except (TimeoutError, asyncio.TimeoutError):
            logger.warning(
                "session drain timed out after %.0fs; forcing close",
                SESSION_DRAIN_TIMEOUT_S,
            )
        except RuntimeError:
            # already closed: the job closes its primary session before the
            # shutdown callbacks run
            logger.debug("session already closed")
        await sess.aclose()


async def entrypoint(ctx: JobContext):
    """Initialize and run the multi-user transcriber."""
    set_job_context(room=ctx.room.name, job_id=ctx.job.id)

    transcriber = MultiUserTranscriber(ctx)
    transcriber.start()

    await ctx.connect(auto_subscribe=AutoSubscribe.AUDIO_ONLY)
    transcriber.watch_transcription()
    for participant in ctx.room.remote_participants.values():
        transcriber.on_participant_connected(participant)

    async def cleanup():
        await transcriber.aclose()

    ctx.add_shutdown_callback(cleanup)


async def handle_transcriber_job_request(job_req: JobRequest) -> None:
    """Accept job if no transcriber exists in room, otherwise reject."""
    room_name = job_req.room.name
    transcriber_id = f"{TRANSCRIBER_AGENT_NAME}-{room_name}"

    async with api.LiveKitAPI() as lkapi:
        try:
            response = await lkapi.room.list_participants(
                list=api.ListParticipantsRequest(room=room_name)
            )

            transcriber_exists = any(
                p.kind == rtc.ParticipantKind.PARTICIPANT_KIND_AGENT
                and p.identity == transcriber_id
                for p in response.participants
            )

            if transcriber_exists:
                logger.info(f"Transcriber exists in {room_name} - rejecting")
                await job_req.reject()
            else:
                logger.info(f"Accepting job for {room_name}")
                await job_req.accept(identity=transcriber_id)

        except Exception:
            logger.exception(f"Error processing job for {room_name}")
            await job_req.reject()


def prewarm(proc: JobProcess):
    """Preload voice activity detection model."""
    configure_sentry(TRANSCRIBER_AGENT_NAME)
    if ENABLE_SILERO_VAD:
        proc.userdata["vad"] = silero.VAD.load()


if __name__ == "__main__":
    # Initialize Sentry for the worker process. Each job runs in its own
    # (forked) process and re-initializes Sentry via prewarm().
    configure_sentry(TRANSCRIBER_AGENT_NAME)
    server = AgentServer.from_server_options(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            request_fnc=handle_transcriber_job_request,
            prewarm_fnc=prewarm,
            agent_name=TRANSCRIBER_AGENT_NAME,
            permissions=WorkerPermissions(hidden=True),
        )
    )
    # LiveKit does not dispatch a job again when its process dies: the worker
    # re-dispatches the transcriber to the transcriptions left without it
    reconciler = TranscriptionReconciler.from_env(TRANSCRIBER_AGENT_NAME)
    if reconciler is not None:
        reconciler.attach(server)
    cli.run_app(server)
