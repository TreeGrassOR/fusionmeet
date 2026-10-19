"""Test the stop-transcription endpoint of the transcripts application."""

# pylint: disable=redefined-outer-name,unused-argument

import uuid
from datetime import timedelta

from django.utils import timezone

import pytest
from livekit.api import TwirpError
from rest_framework.test import APIClient

from core.factories import RoomFactory, UserFactory
from core.models import RoleChoices, RoomAccessLevel

from transcripts.factories import TranscriptFactory
from transcripts.models import Transcript, TranscriptStatusChoices
from transcripts.services import STOPPING_TIMEOUT_ERROR

pytestmark = pytest.mark.django_db


def stop_url(room):
    """URL of the stop-transcription endpoint of a room."""
    return f"/api/v1.0/rooms/{room.id}/stop-transcription/"


def owner_client(room, role=RoleChoices.OWNER):
    """Return a client logged in as a user with a role in the room."""
    user = UserFactory()
    room.accesses.create(user=user, role=role)
    client = APIClient()
    client.force_login(user)
    return client


@pytest.mark.parametrize("enabled, expected_status", [(True, 401), (False, 404)])
def test_stop_transcription_anonymous(settings, livekit, enabled, expected_status):
    """Anonymous users cannot stop a transcription."""
    settings.TRANSCRIPTION_ENABLED = enabled
    room = RoomFactory()
    transcript = TranscriptFactory(room=room)

    response = APIClient().post(stop_url(room))

    assert response.status_code == expected_status
    transcript.refresh_from_db()
    assert transcript.status == TranscriptStatusChoices.ACTIVE


def test_stop_transcription_disabled(settings, livekit):
    """The endpoint does not exist when the feature is disabled."""
    settings.TRANSCRIPTION_ENABLED = False
    room = RoomFactory()
    TranscriptFactory(room=room)

    response = owner_client(room).post(stop_url(room))

    assert response.status_code == 404


def test_stop_transcription_restricted_room_member(transcription_enabled, livekit):
    """Members without owner/administrator role cannot stop a transcription."""
    room = RoomFactory(access_level=RoomAccessLevel.RESTRICTED)
    transcript = TranscriptFactory(room=room)

    response = owner_client(room, role=RoleChoices.MEMBER).post(stop_url(room))

    assert response.status_code == 403
    transcript.refresh_from_db()
    assert transcript.status == TranscriptStatusChoices.ACTIVE


def test_stop_transcription_none_active(transcription_enabled, livekit):
    """A 404 is returned when no transcription is active in the room."""
    room = RoomFactory()
    TranscriptFactory(room=room, status=TranscriptStatusChoices.STOPPING)
    TranscriptFactory(room=room, status=TranscriptStatusChoices.READY)
    TranscriptFactory(status=TranscriptStatusChoices.ACTIVE)

    response = owner_client(room).post(stop_url(room))

    assert response.status_code == 404
    assert response.json() == {"detail": "No active transcription found for this room."}
    livekit.client.room.update_room_metadata.assert_not_called()


NOT_FOUND = {"detail": "No active transcription found for this room."}


@pytest.mark.parametrize(
    "status", [TranscriptStatusChoices.READY, TranscriptStatusChoices.FAILED]
)
def test_stop_transcription_none_active_completed_metadata_removed(
    transcription_enabled, livekit, status
):
    """A key left over by a completed transcript is removed (still a 404)."""
    room = RoomFactory()
    done = TranscriptFactory(room=room, status=status)
    livekit.metadata = {
        "recording_status": "started",
        "transcription": done.get_room_metadata("stopping"),
    }

    response = owner_client(room).post(stop_url(room))

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    assert livekit.metadata == {"recording_status": "started"}
    done.refresh_from_db()
    assert done.status == status


@pytest.mark.parametrize("kind", ["unknown", "other_room", "malformed"])
def test_stop_transcription_none_active_unknown_metadata_removed(
    transcription_enabled, livekit, kind
):
    """A key referring to no transcript of the room is removed."""
    room = RoomFactory()
    other_room_transcript = TranscriptFactory(status=TranscriptStatusChoices.ACTIVE)
    entry = {
        "unknown": {"id": str(uuid.uuid4()), "status": "started"},
        "other_room": other_room_transcript.get_room_metadata("started"),
        "malformed": {"id": "not-a-uuid", "status": "started"},
    }[kind]
    livekit.metadata = {"access_level": "public", "transcription": entry}

    response = owner_client(room).post(stop_url(room))

    assert response.status_code == 404
    assert livekit.metadata == {"access_level": "public"}
    other_room_transcript.refresh_from_db()
    assert other_room_transcript.status == TranscriptStatusChoices.ACTIVE


@pytest.mark.parametrize(
    "timeout, stopped_seconds_ago, is_stale",
    [
        (180, 0, False),
        (180, 180, False),  # boundary: stale only once the timeout is exceeded
        (180, 181, True),
        (30, 31, True),
    ],
)
@pytest.mark.usefixtures("frozen_now")
def test_stop_transcription_none_active_stopping_timeout(
    transcription_enabled, livekit, timeout, stopped_seconds_ago, is_stale
):
    """A transcript stopping for too long is failed and its key removed."""
    transcription_enabled.TRANSCRIPTION_STOPPING_TIMEOUT_SECONDS = timeout
    room = RoomFactory()
    stopped_at = timezone.now() - timedelta(seconds=stopped_seconds_ago)
    stopping = TranscriptFactory(
        room=room, status=TranscriptStatusChoices.STOPPING, ended_at=stopped_at
    )
    metadata = {
        "recording_status": "started",
        "transcription": stopping.get_room_metadata("stopping"),
    }
    livekit.metadata = metadata

    response = owner_client(room).post(stop_url(room))

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    stopping.refresh_from_db()
    if is_stale:
        assert stopping.status == TranscriptStatusChoices.FAILED
        assert stopping.error == STOPPING_TIMEOUT_ERROR
        assert stopping.ended_at == stopped_at
        assert livekit.metadata == {"recording_status": "started"}
    else:
        # the agent is still finalizing it: its hook removes the key
        assert stopping.status == TranscriptStatusChoices.STOPPING
        assert livekit.metadata == metadata
        livekit.client.room.update_room_metadata.assert_not_called()


def test_stop_transcription_none_active_room_not_live(transcription_enabled, livekit):
    """Without a LiveKit room there is no metadata to clean."""
    livekit.live = False
    room = RoomFactory()
    TranscriptFactory(room=room, status=TranscriptStatusChoices.READY)

    response = owner_client(room).post(stop_url(room))

    assert response.status_code == 404
    livekit.client.room.update_room_metadata.assert_not_called()


@pytest.mark.parametrize("failing_call", ["list_rooms", "update_room_metadata"])
def test_stop_transcription_none_active_livekit_error(
    transcription_enabled, livekit, failing_call
):
    """LiveKit errors while cleaning a stale key keep the 404 response."""
    room = RoomFactory()
    done = TranscriptFactory(room=room, status=TranscriptStatusChoices.READY)
    livekit.metadata = {"transcription": done.get_room_metadata("stopping")}
    if failing_call == "list_rooms":
        livekit.client.room.list_rooms.side_effect = OSError("LiveKit is down")
    else:
        livekit.client.room.update_room_metadata.side_effect = TwirpError(
            "internal", "boom", status=500
        )

    response = owner_client(room).post(stop_url(room))

    assert response.status_code == 404
    assert response.json() == NOT_FOUND
    assert livekit.metadata == {"transcription": done.get_room_metadata("stopping")}


@pytest.mark.parametrize("role", [RoleChoices.OWNER, RoleChoices.ADMIN])
def test_stop_transcription_success(transcription_enabled, livekit, role):
    """Stopping sets the transcript and the room metadata to stopping."""
    room = RoomFactory()
    starter = UserFactory()
    transcript = TranscriptFactory(room=room, started_by=starter)
    livekit.metadata = {
        "recording_status": "started",
        "transcription": transcript.get_room_metadata("started"),
    }

    response = owner_client(room, role=role).post(stop_url(room))

    assert response.status_code == 200
    transcript.refresh_from_db()
    assert transcript.status == TranscriptStatusChoices.STOPPING
    assert transcript.ended_at is not None
    assert response.json()["id"] == str(transcript.id)
    assert response.json()["status"] == "stopping"
    assert livekit.metadata == {
        "recording_status": "started",
        "transcription": {
            **transcript.get_room_metadata("started"),
            "status": "stopping",
        },
    }
    assert livekit.metadata["transcription"]["started_by"] == starter.full_name
    assert livekit.dispatches == []


@pytest.mark.parametrize(
    "concurrent_status, key_kept",
    [
        # The agent hook completed it and removed the key just before the stop
        # wrote it again: no "stopping" key must be left, nothing would remove it.
        (TranscriptStatusChoices.READY, False),
        (TranscriptStatusChoices.FAILED, False),
        # A concurrent stop: the agent is still finalizing, the key is kept.
        (TranscriptStatusChoices.STOPPING, True),
    ],
)
def test_stop_transcription_concurrent_update(
    transcription_enabled, livekit, concurrent_status, key_kept
):
    """The status set concurrently during a stop decides if the key is kept."""
    room = RoomFactory()
    transcript = TranscriptFactory(room=room)
    livekit.metadata = {"recording_status": "started"}
    write_metadata = livekit.client.room.update_room_metadata.side_effect

    async def concurrent_update_before_write(request):
        if livekit.client.room.update_room_metadata.await_count == 1:
            await Transcript.objects.filter(pk=transcript.pk).aupdate(
                status=concurrent_status
            )
        return await write_metadata(request)

    livekit.client.room.update_room_metadata.side_effect = (
        concurrent_update_before_write
    )

    response = owner_client(room).post(stop_url(room))

    assert response.status_code == 200
    assert response.json()["status"] == concurrent_status
    transcript.refresh_from_db()
    assert transcript.status == concurrent_status
    if key_kept:
        assert livekit.metadata == {
            "recording_status": "started",
            "transcription": transcript.get_room_metadata("stopping"),
        }
    else:
        assert livekit.metadata == {"recording_status": "started"}


def test_stop_transcription_room_not_live(transcription_enabled, livekit):
    """When the call is over, the transcript still switches to stopping."""
    livekit.live = False
    room = RoomFactory()
    transcript = TranscriptFactory(room=room)

    response = owner_client(room).post(stop_url(room))

    assert response.status_code == 200
    transcript.refresh_from_db()
    assert transcript.status == TranscriptStatusChoices.STOPPING


def test_stop_transcription_metadata_failure(transcription_enabled, livekit):
    """A metadata write failure gives a 502 and leaves the transcript active."""
    livekit.client.room.update_room_metadata.side_effect = TwirpError(
        "internal", "boom", status=500
    )
    room = RoomFactory()
    transcript = TranscriptFactory(room=room)

    response = owner_client(room).post(stop_url(room))

    assert response.status_code == 502
    assert response.json() == {
        "error": f"Transcription failed to stop for room {room.slug}"
    }
    transcript.refresh_from_db()
    assert transcript.status == TranscriptStatusChoices.ACTIVE
    assert transcript.ended_at is None
