"""Transcription control: LiveKit room metadata, agent dispatch and notifications.

The backend writes the "transcription" key of the LiveKit room metadata, signed
for the room (see `models.sign_room_transcript`): room owners and administrators
can also write the metadata, the agent ignores entries with a wrong signature.
The transcriber agent follows that key: it stores phrases while a transcription
is "started", finalizes the files when it turns to "stopping" (or when the call
ends), then reports the result through the agent hook.
"""

# pylint: disable=no-name-in-module

import json
import uuid
from datetime import timedelta
from logging import getLogger

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Value
from django.db.models.functions import Coalesce
from django.utils import timezone
from django.utils.translation import get_language, gettext_noop

from asgiref.sync import async_to_sync
from livekit.api import ListRoomsRequest
from livekit.protocol.agent_dispatch import CreateAgentDispatchRequest

from core import utils
from core.services.room_management import (
    RoomManagement,
    RoomManagementException,
    RoomNotFoundException,
)
from core.tasks.mail import send_email

from .models import Transcript, TranscriptStatusChoices

logger = getLogger(__name__)

METADATA_KEY = "transcription"
METADATA_STATUS_STARTED = "started"
METADATA_STATUS_STOPPING = "stopping"

# Keep the agent error message stored in the database reasonably small.
MAX_ERROR_LENGTH = 2000

STOPPING_TIMEOUT_ERROR = (
    "The transcription agent did not complete the transcript in time."
)


class TranscriptionException(Exception):
    """Base exception of the transcription service."""


class TranscriptionAlreadyStartedError(TranscriptionException):
    """A transcription is already running in the room."""


class RoomNotLiveError(TranscriptionException):
    """The room does not exist in LiveKit: nobody is connected to it."""


class TranscriptionStartError(TranscriptionException):
    """LiveKit could not be updated to start the transcription."""


class TranscriptionNotFoundError(TranscriptionException):
    """No transcription is running in the room."""


class TranscriptionStopError(TranscriptionException):
    """LiveKit could not be updated to stop the transcription."""


@async_to_sync
async def get_room_metadata(room_name: str):
    """Read the metadata of a LiveKit room.

    Returns:
        The metadata as a dict, or None when the room does not exist in LiveKit.

    Raises:
        RoomManagementException: LiveKit could not be reached.
    """

    lkapi = utils.create_livekit_client()

    try:
        response = await lkapi.room.list_rooms(ListRoomsRequest(names=[room_name]))
    except Exception as e:  # pylint: disable=broad-exception-caught
        logger.exception("Could not read the metadata of room %s", room_name)
        raise RoomManagementException("Could not read room metadata") from e
    finally:
        await lkapi.aclose()

    if not response.rooms:
        return None

    try:
        metadata = json.loads(response.rooms[0].metadata or "{}")
    except ValueError:
        logger.warning("Room %s has invalid JSON metadata", room_name)
        return {}

    return metadata if isinstance(metadata, dict) else {}


@async_to_sync
async def dispatch_agent(room_name: str, transcript_id: str):
    """Dispatch the transcriber agent to a room and return the dispatch id.

    The agent deduplicates its jobs per room: when it already runs in the room
    (live subtitles), the new job is rejected and the running agent picks the
    transcription up from the room metadata.
    """

    lkapi = utils.create_livekit_client()

    try:
        response = await lkapi.agent_dispatch.create_dispatch(
            CreateAgentDispatchRequest(
                agent_name=settings.TRANSCRIPTION_AGENT_NAME,
                room=room_name,
                metadata=json.dumps({"transcript_id": transcript_id}),
            )
        )
    finally:
        await lkapi.aclose()

    return getattr(response, "id", None) or None


def get_metadata_transcript_id(metadata):
    """Return the transcript id the room metadata refers to, if any."""
    value = (metadata or {}).get(METADATA_KEY)
    if not isinstance(value, dict):
        return None

    try:
        return str(uuid.UUID(str(value.get("id"))))
    except ValueError:
        return None


def is_stopping_timed_out(transcript) -> bool:
    """Whether a transcript has been stopping for too long.

    The agent completes a stopping transcript within seconds: past
    TRANSCRIPTION_STOPPING_TIMEOUT_SECONDS it is dead or stuck, and the
    "stopping" room metadata must not block the room anymore.
    """
    if transcript.status != TranscriptStatusChoices.STOPPING:
        return False

    stopped_at = transcript.ended_at or transcript.updated_at
    timeout = timedelta(seconds=settings.TRANSCRIPTION_STOPPING_TIMEOUT_SECONDS)
    return stopped_at < timezone.now() - timeout


def get_transcription_list_url() -> str:
    """URL of the "My recordings and transcripts" page, used in emails."""
    if settings.TRANSCRIPTION_LIST_URL:
        return settings.TRANSCRIPTION_LIST_URL

    base_url = (settings.EMAIL_APP_BASE_URL or "").rstrip("/")
    return f"{base_url}/my-recordings"


class TranscriptionService:
    """Start, stop and complete transcriptions."""

    @staticmethod
    def _mark_failed(transcript, error):
        """Mark a transcript as failed, outside of the model validation."""
        now = timezone.now()
        Transcript.objects.filter(pk=transcript.pk).update(
            status=TranscriptStatusChoices.FAILED,
            error=error,
            ended_at=Coalesce(F("ended_at"), Value(now)),
        )
        transcript.refresh_from_db()

    @staticmethod
    def _fail_stale_transcripts(room):
        """Fail ongoing transcripts of a room that no room metadata refers to.

        The agent only works on the transcript referenced by the room metadata:
        the others will never be completed (e.g. the call ended and the agent
        could not reach the backend).
        """
        updated = Transcript.objects.filter(
            room=room, status__in=TranscriptStatusChoices.ongoing()
        ).update(
            status=TranscriptStatusChoices.FAILED,
            error="Stale transcription: the room metadata no longer refers to it.",
            ended_at=Coalesce(F("ended_at"), Value(timezone.now())),
        )
        if updated:
            logger.warning(
                "Marked %s stale transcript(s) of room %s as failed", updated, room.id
            )

    @staticmethod
    def _fail_timed_out_transcript(transcript):
        """Fail a transcript the agent did not complete in time after a stop.

        Conditional update: the agent hook may complete it concurrently. A late
        ready result is still applied by the hook.
        """
        updated = Transcript.objects.filter(
            pk=transcript.pk, status=TranscriptStatusChoices.STOPPING
        ).update(
            status=TranscriptStatusChoices.FAILED,
            error=STOPPING_TIMEOUT_ERROR,
            ended_at=Coalesce(F("ended_at"), Value(timezone.now())),
        )
        if updated:
            logger.warning(
                "Transcript %s was stopping for too long, marked as failed",
                transcript.id,
            )

    def _is_metadata_stale(self, room, metadata) -> bool:
        """Whether the "transcription" metadata key is free to be replaced.

        The key is stale unless it refers to an ongoing transcript of this room:
        it refers to a ready, failed or unknown transcript (e.g. of another
        room), or to a transcript stopping for longer than
        TRANSCRIPTION_STOPPING_TIMEOUT_SECONDS, which is then marked as failed.
        """
        transcript_id = get_metadata_transcript_id(metadata)
        transcript = (
            Transcript.objects.filter(pk=transcript_id, room=room).first()
            if transcript_id
            else None
        )
        if transcript is None or transcript.status not in (
            TranscriptStatusChoices.ongoing()
        ):
            return True

        if not is_stopping_timed_out(transcript):
            return False

        self._fail_timed_out_transcript(transcript)
        return True

    def _remove_stale_metadata(self, room) -> None:
        """Remove a "transcription" metadata key that nothing will complete.

        Called by a stop without an active transcript: otherwise a stale key
        would keep showing a running transcription to the participants. A key
        of a transcript still stopping within the timeout is kept: the agent is
        finalizing it and its hook removes the key.
        """
        room_name = str(room.id)
        try:
            metadata = get_room_metadata(room_name)
        except RoomManagementException:
            return

        if not metadata or METADATA_KEY not in metadata:
            return
        if not self._is_metadata_stale(room, metadata):
            return

        try:
            RoomManagement.update_metadata(
                room_name=room_name, remove_keys=[METADATA_KEY]
            )
        except RoomNotFoundException:
            return
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception(
                "Could not remove the stale transcription metadata of room %s",
                room_name,
            )
            return

        logger.info("Removed the stale transcription metadata of room %s", room_name)

    @staticmethod
    def remove_room_metadata(room_name: str, transcript_id) -> None:
        """Remove the "transcription" metadata key if it refers to this transcript.

        Failures are logged: the agent no longer depends on the key once it has
        reported the result, and a later start overwrites a stale key.
        """
        try:
            metadata = get_room_metadata(room_name)
            if get_metadata_transcript_id(metadata) != str(transcript_id):
                return
            RoomManagement.update_metadata(
                room_name=room_name, remove_keys=[METADATA_KEY]
            )
        except RoomNotFoundException:
            return
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception(
                "Could not remove the transcription metadata of room %s", room_name
            )

    def start(self, room, user):
        """Start a transcription in a room on behalf of a user.

        Raises:
            TranscriptionAlreadyStartedError: a transcription is already running.
            RoomNotLiveError: the room does not exist in LiveKit.
            TranscriptionStartError: LiveKit could not be updated.
        """
        room_name = str(room.id)

        try:
            metadata = get_room_metadata(room_name)
        except RoomManagementException as e:
            raise TranscriptionStartError("Could not read the room metadata") from e

        if metadata is None:
            raise RoomNotLiveError("The room is not live")

        # A stale key (completed, unknown or timed out transcript) is overwritten
        if not self._is_metadata_stale(room, metadata):
            raise TranscriptionAlreadyStartedError("A transcription is running")

        self._fail_stale_transcripts(room)

        try:
            with transaction.atomic():
                transcript = Transcript.objects.create(
                    room=room,
                    started_by=user,
                    status=TranscriptStatusChoices.ACTIVE,
                    language=settings.TRANSCRIPTION_LANGUAGE,
                    started_at=timezone.now(),
                )
                transcript.grant_initial_accesses(starter=user)
        except (DjangoValidationError, IntegrityError) as e:
            # A concurrent request created the ongoing transcript of the room first
            raise TranscriptionAlreadyStartedError("A transcription is running") from e

        try:
            RoomManagement.update_metadata(
                room_name=room_name,
                metadata={
                    METADATA_KEY: transcript.get_room_metadata(METADATA_STATUS_STARTED)
                },
            )
        except RoomNotFoundException as e:
            self._mark_failed(transcript, "The room was not live.")
            raise RoomNotLiveError("The room is not live") from e
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.exception("Could not write the transcription metadata")
            self._mark_failed(transcript, "Could not update the room metadata.")
            raise TranscriptionStartError("Could not update the room metadata") from e

        try:
            dispatch_id = dispatch_agent(room_name, str(transcript.id))
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.exception("Could not dispatch the transcriber to room %s", room_name)
            self._mark_failed(transcript, "Could not dispatch the transcription agent.")
            self.remove_room_metadata(room_name, transcript.id)
            raise TranscriptionStartError("Could not dispatch the agent") from e

        if dispatch_id:
            Transcript.objects.filter(pk=transcript.pk).update(dispatch_id=dispatch_id)
            transcript.dispatch_id = dispatch_id

        logger.info("Transcription %s started in room %s", transcript.id, room_name)
        return transcript

    def stop(self, room):
        """Ask the agent to finalize the running transcription of a room.

        Raises:
            TranscriptionNotFoundError: no transcription is running in the room.
            TranscriptionStopError: LiveKit could not be updated.
        """
        transcript = (
            Transcript.objects.select_related("room", "started_by")
            .filter(room=room, status=TranscriptStatusChoices.ACTIVE)
            .first()
        )
        if transcript is None:
            self._remove_stale_metadata(room)
            raise TranscriptionNotFoundError("No active transcription")

        room_name = str(room.id)
        try:
            RoomManagement.update_metadata(
                room_name=room_name,
                metadata={
                    METADATA_KEY: transcript.get_room_metadata(METADATA_STATUS_STOPPING)
                },
            )
        except RoomNotFoundException:
            # The call is over: the agent finalizes the transcript when it leaves.
            logger.info("Room %s is not live, transcription stops with it", room_name)
        except Exception as e:  # pylint: disable=broad-exception-caught
            logger.exception("Could not write the transcription metadata")
            raise TranscriptionStopError("Could not update the room metadata") from e

        # Conditional update: the agent hook may already have completed it.
        Transcript.objects.filter(
            pk=transcript.pk, status=TranscriptStatusChoices.ACTIVE
        ).update(status=TranscriptStatusChoices.STOPPING, ended_at=timezone.now())
        transcript.refresh_from_db()

        if transcript.status not in TranscriptStatusChoices.ongoing():
            # The hook may have removed the key before it was written again
            # above: do not leave a "stopping" key that nothing would remove.
            self.remove_room_metadata(room_name, transcript.id)

        logger.info("Transcription %s stopping in room %s", transcript.id, room_name)
        return transcript

    def complete(self, transcript, data) -> bool:
        """Apply the result reported by the agent hook (idempotent).

        A result is applied whatever the current status, except that a failure
        never overrides a ready transcript.

        Returns:
            True when the transcript has just become ready.
        """
        # Remove the metadata first: a start racing with this hook sees either
        # the key of a still ongoing transcript (409) or no key at all.
        self.remove_room_metadata(str(transcript.room_id), transcript.id)

        new_status = data["status"]
        files = data.get("files") or {}

        with transaction.atomic():
            locked = Transcript.objects.select_for_update().get(pk=transcript.pk)
            was_ready = locked.is_ready

            if was_ready and new_status == TranscriptStatusChoices.FAILED:
                logger.warning(
                    "Ignoring a failure reported for ready transcript %s", locked.id
                )
                return False

            locked.status = new_status
            if data.get("started_at"):
                locked.started_at = data["started_at"]
            locked.ended_at = data.get("ended_at") or locked.ended_at or timezone.now()
            locked.participants = [
                {"identity": item["identity"], "name": item.get("name") or ""}
                for item in data.get("participants") or []
            ]
            locked.segments_count = data.get("segments_count") or 0
            locked.json_key = files.get("json") or None
            locked.md_key = files.get("md") or None
            locked.error = (
                ""
                if new_status == TranscriptStatusChoices.READY
                else (data.get("error") or "Transcription failed.")[:MAX_ERROR_LENGTH]
            )
            locked.save()

        transcript.refresh_from_db()
        logger.info("Transcript %s is %s", transcript.id, transcript.status)
        return transcript.is_ready and not was_ready


def notify_transcript_ready(transcript) -> bool:
    """Email the initiator of a transcription that the transcript is ready.

    Only sent when SMTP is configured (EMAIL_HOST). Errors are logged and never
    raised: the transcript is ready and listed whether or not the email is sent.
    """
    if not settings.EMAIL_HOST:
        logger.info("SMTP is not configured, no email for transcript %s", transcript.id)
        return False

    user = transcript.started_by
    if user is None or not user.email:
        return False

    try:
        local_started_at = timezone.localtime(transcript.started_at, user.timezone)
        send_email.delay(
            template="transcript_ready",
            subject=gettext_noop("Your transcript is ready"),
            recipients=[user.email],
            language=user.language or get_language(),
            context={
                "room_name": transcript.room.name,
                "transcript_date": local_started_at.strftime("%Y-%m-%d"),
                "transcript_time": local_started_at.strftime("%H:%M"),
                "link": get_transcription_list_url(),
            },
        )
    except Exception:  # pylint: disable=broad-exception-caught
        logger.exception("Could not send the email of transcript %s", transcript.id)
        return False

    return True
