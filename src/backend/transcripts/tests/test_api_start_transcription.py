"""Test the start-transcription endpoint of the transcripts application."""

# pylint: disable=redefined-outer-name,unused-argument

import hashlib
import hmac
import json
import uuid
from datetime import timedelta
from unittest import mock

from django.utils import timezone

import pytest
from livekit.api import AccessToken, TwirpError, VideoGrants
from rest_framework.test import APIClient

from core.factories import RecordingFactory, RoomFactory, UserFactory
from core.models import RecordingStatusChoices, RoleChoices, RoomAccessLevel

from transcripts.factories import TranscriptFactory
from transcripts.models import Transcript, TranscriptStatusChoices
from transcripts.services import STOPPING_TIMEOUT_ERROR

pytestmark = pytest.mark.django_db


def start_url(room):
    """URL of the start-transcription endpoint of a room."""
    return f"/api/v1.0/rooms/{room.id}/start-transcription/"


def owner_client(room, role=RoleChoices.OWNER):
    """Return a client logged in as a user with a role in the room, and the user."""
    user = UserFactory()
    room.accesses.create(user=user, role=role)
    client = APIClient()
    client.force_login(user)
    return client, user


@pytest.mark.parametrize("enabled, expected_status", [(True, 401), (False, 404)])
def test_start_transcription_anonymous(settings, livekit, enabled, expected_status):
    """Anonymous users cannot start a transcription."""
    settings.TRANSCRIPTION_ENABLED = enabled
    room = RoomFactory()

    response = APIClient().post(start_url(room))

    assert response.status_code == expected_status
    assert Transcript.objects.count() == 0
    assert livekit.dispatches == []


def test_start_transcription_disabled(settings, livekit):
    """The endpoint does not exist when the feature is disabled."""
    settings.TRANSCRIPTION_ENABLED = False
    room = RoomFactory()
    client, _ = owner_client(room)

    response = client.post(start_url(room))

    assert response.status_code == 404
    assert response.json() == {"detail": "Not found."}
    assert Transcript.objects.count() == 0


@pytest.mark.parametrize("authenticated_participants_enabled", [True, False])
def test_start_transcription_restricted_room_member(
    transcription_enabled, livekit, authenticated_participants_enabled
):
    """Members of a restricted room without owner/administrator role get a 403."""
    transcription_enabled.RECORDING_AUTHENTICATED_PARTICIPANTS_ENABLED = (
        authenticated_participants_enabled
    )
    room = RoomFactory(access_level=RoomAccessLevel.RESTRICTED)
    client, _ = owner_client(room, role=RoleChoices.MEMBER)

    response = client.post(start_url(room))

    assert response.status_code == 403
    assert Transcript.objects.count() == 0
    assert livekit.dispatches == []


@mock.patch(
    "core.services.participants_management.ParticipantsManagement.check_if_in_meeting",
    return_value=False,
)
def test_start_transcription_open_room_absent_user(
    mock_check, transcription_enabled, livekit
):
    """An authenticated user who is not in the meeting gets a 403."""
    room = RoomFactory(access_level=RoomAccessLevel.PUBLIC)
    user = UserFactory()
    client = APIClient()
    client.force_login(user)

    response = client.post(start_url(room))

    assert response.status_code == 403
    assert response.json() == {"detail": "You are not allowed to perform this action."}
    assert Transcript.objects.count() == 0


@pytest.mark.parametrize(
    "access_level", [RoomAccessLevel.TRUSTED, RoomAccessLevel.PUBLIC]
)
@mock.patch(
    "core.services.participants_management.ParticipantsManagement.check_if_in_meeting",
    return_value=True,
)
def test_start_transcription_open_room_present_user(
    mock_check, transcription_enabled, livekit, access_level
):
    """Like the recording, authenticated participants of an open room can start."""
    room = RoomFactory(access_level=access_level)
    user = UserFactory()
    client = APIClient()
    client.force_login(user)

    response = client.post(start_url(room))

    assert response.status_code == 201
    mock_check.assert_called_once_with(room_name=str(room.id), identity=str(user.sub))
    transcript = Transcript.objects.get()
    assert transcript.started_by == user
    assert list(transcript.users.all()) == [user]


@mock.patch(
    "core.services.participants_management.ParticipantsManagement.check_if_in_meeting",
    return_value=True,
)
def test_start_transcription_open_room_present_user_setting_disabled(
    mock_check, transcription_enabled, livekit
):
    """Present participants cannot start when the recording setting forbids it."""
    transcription_enabled.RECORDING_AUTHENTICATED_PARTICIPANTS_ENABLED = False
    room = RoomFactory(access_level=RoomAccessLevel.PUBLIC)
    user = UserFactory()
    client = APIClient()
    client.force_login(user)

    response = client.post(start_url(room))

    assert response.status_code == 403
    assert Transcript.objects.count() == 0


def test_start_transcription_livekit_token_rejected(transcription_enabled, livekit):
    """A LiveKit room token is not accepted, even for the room owner."""
    room = RoomFactory()
    owner = UserFactory()
    room.accesses.create(user=owner, role=RoleChoices.OWNER)
    token = (
        AccessToken(
            api_key=transcription_enabled.LIVEKIT_CONFIGURATION["api_key"],
            api_secret=transcription_enabled.LIVEKIT_CONFIGURATION["api_secret"],
        )
        .with_grants(VideoGrants(room=str(room.id), room_join=True, room_admin=True))
        .with_identity(str(owner.sub))
        .to_jwt()
    )

    response = APIClient().post(
        start_url(room), HTTP_AUTHORIZATION=f"X-LiveKit-Token {token}"
    )

    assert response.status_code == 401
    assert Transcript.objects.count() == 0
    assert livekit.dispatches == []


@pytest.mark.parametrize("role", [RoleChoices.OWNER, RoleChoices.ADMIN])
def test_start_transcription_success(transcription_enabled, livekit, role):
    """Owners and administrators start a transcription: row, metadata, dispatch."""
    transcription_enabled.TRANSCRIPTION_LANGUAGE = "ru"
    transcription_enabled.TRANSCRIPTION_AGENT_NAME = "custom-transcriber"
    transcription_enabled.TRANSCRIPTION_AGENT_TOKEN = "agent-token"
    room = RoomFactory(name="Daily meeting")
    other_owner = UserFactory()
    room.accesses.create(user=other_owner, role=RoleChoices.OWNER)
    room.accesses.create(user=UserFactory(), role=RoleChoices.MEMBER)
    livekit.metadata = {
        "recording_mode": "screen_recording",
        "recording_status": "started",
        "access_level": "public",
    }
    client, user = owner_client(room, role=role)

    response = client.post(start_url(room))

    assert response.status_code == 201
    transcript = Transcript.objects.get()
    assert transcript.status == TranscriptStatusChoices.ACTIVE
    assert transcript.started_by == user
    assert transcript.language == "ru"
    assert transcript.dispatch_id == "AD_test"
    assert transcript.ended_at is None
    assert set(transcript.users.all()) == {user, other_owner}

    assert response.json() == {
        "id": str(transcript.id),
        "room": {"id": str(room.id), "name": "Daily meeting", "slug": room.slug},
        "status": "active",
        "language": "ru",
        "started_at": transcript.started_at.isoformat().replace("+00:00", "Z"),
        "ended_at": None,
        "created_at": transcript.created_at.isoformat().replace("+00:00", "Z"),
        "participants": [],
        "segments_count": 0,
        "models": [],
        "is_ready": False,
        "downloads": {"md": None, "json": None},
    }

    # Room metadata is merged: the recording keys are kept
    assert livekit.metadata == {
        "recording_mode": "screen_recording",
        "recording_status": "started",
        "access_level": "public",
        "transcription": {
            "id": str(transcript.id),
            "status": "started",
            "room_name": "Daily meeting",
            "started_at": transcript.started_at.strftime("%Y-%m-%dT%H:%M:%S.")
            + f"{transcript.started_at.microsecond // 1000:03d}Z",
            "started_by": user.full_name,
            # signed for this room: the agent ignores entries copied elsewhere
            "sig": hmac.new(
                b"agent-token",
                f"{room.id}:{transcript.id}".encode(),
                hashlib.sha256,
            ).hexdigest(),
        },
    }

    assert len(livekit.dispatches) == 1
    dispatch = livekit.dispatches[0]
    assert dispatch.agent_name == "custom-transcriber"
    assert dispatch.room == str(room.id)
    assert json.loads(dispatch.metadata) == {"transcript_id": str(transcript.id)}


def test_start_transcription_while_recording(transcription_enabled, livekit):
    """Transcription is independent from a running recording of the room."""
    room = RoomFactory()
    RecordingFactory(room=room, status=RecordingStatusChoices.ACTIVE)
    client, _ = owner_client(room)

    response = client.post(start_url(room))

    assert response.status_code == 201
    assert Transcript.objects.filter(status=TranscriptStatusChoices.ACTIVE).count() == 1
    assert room.recordings.get().status == RecordingStatusChoices.ACTIVE


@pytest.mark.parametrize(
    "status", [TranscriptStatusChoices.ACTIVE, TranscriptStatusChoices.STOPPING]
)
def test_start_transcription_already_running(transcription_enabled, livekit, status):
    """A 409 is returned while the room metadata refers to an ongoing transcript."""
    room = RoomFactory()
    running = TranscriptFactory(room=room, status=status)
    livekit.metadata = {
        "transcription": running.get_room_metadata("started"),
        "access_level": "public",
    }
    client, _ = owner_client(room)

    response = client.post(start_url(room))

    assert response.status_code == 409
    assert response.json() == {
        "error": f"A transcription is already in progress for room {room.slug}"
    }
    assert Transcript.objects.get() == running
    running.refresh_from_db()
    assert running.status == status
    assert livekit.dispatches == []
    livekit.client.room.update_room_metadata.assert_not_called()


@pytest.mark.parametrize(
    "status", [TranscriptStatusChoices.ACTIVE, TranscriptStatusChoices.STOPPING]
)
def test_start_transcription_stale_rows_marked_failed(
    transcription_enabled, livekit, status
):
    """Ongoing rows that no metadata refers to are failed, then a new one starts."""
    room = RoomFactory()
    stale = TranscriptFactory(room=room, status=status)
    other_room_transcript = TranscriptFactory(status=TranscriptStatusChoices.ACTIVE)
    livekit.metadata = {"access_level": "public"}
    client, _ = owner_client(room)

    response = client.post(start_url(room))

    assert response.status_code == 201
    stale.refresh_from_db()
    assert stale.status == TranscriptStatusChoices.FAILED
    assert stale.ended_at is not None
    assert "Stale" in stale.error
    other_room_transcript.refresh_from_db()
    assert other_room_transcript.status == TranscriptStatusChoices.ACTIVE

    new = Transcript.objects.get(room=room, status=TranscriptStatusChoices.ACTIVE)
    assert livekit.metadata["transcription"]["id"] == str(new.id)


@pytest.mark.parametrize(
    "status", [TranscriptStatusChoices.READY, TranscriptStatusChoices.FAILED]
)
def test_start_transcription_stale_metadata(transcription_enabled, livekit, status):
    """Metadata left over by a completed transcript does not block a new start."""
    room = RoomFactory()
    done = TranscriptFactory(room=room, status=status)
    livekit.metadata = {"transcription": done.get_room_metadata("stopping")}
    client, _ = owner_client(room)

    response = client.post(start_url(room))

    assert response.status_code == 201
    assert livekit.metadata["transcription"]["id"] == response.json()["id"]
    assert livekit.metadata["transcription"]["status"] == "started"


@pytest.mark.parametrize(
    "timeout, stopped_seconds_ago, is_stale",
    [
        (180, 0, False),
        (180, 179, False),
        (180, 180, False),  # boundary: stale only once the timeout is exceeded
        (180, 181, True),
        (180, 3600, True),
        (30, 29, False),
        (30, 31, True),
    ],
)
@pytest.mark.usefixtures("frozen_now")
def test_start_transcription_stopping_timeout(
    transcription_enabled, livekit, timeout, stopped_seconds_ago, is_stale
):
    """A transcript stopping for longer than the timeout no longer blocks a start."""
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
    client, _ = owner_client(room)

    response = client.post(start_url(room))

    stopping.refresh_from_db()
    if not is_stale:
        assert response.status_code == 409
        assert stopping.status == TranscriptStatusChoices.STOPPING
        assert livekit.metadata == metadata
        assert livekit.dispatches == []
        return

    assert response.status_code == 201
    assert stopping.status == TranscriptStatusChoices.FAILED
    assert stopping.error == STOPPING_TIMEOUT_ERROR
    assert stopping.ended_at == stopped_at
    new = Transcript.objects.get(room=room, status=TranscriptStatusChoices.ACTIVE)
    assert livekit.metadata == {
        "recording_status": "started",
        "transcription": new.get_room_metadata("started"),
    }
    assert len(livekit.dispatches) == 1


def test_start_transcription_stopping_without_end_date(transcription_enabled, livekit):
    """Without an end date, the last update of a stopping transcript is used."""
    transcription_enabled.TRANSCRIPTION_STOPPING_TIMEOUT_SECONDS = 180
    room = RoomFactory()
    stopping = TranscriptFactory(room=room, status=TranscriptStatusChoices.STOPPING)
    livekit.metadata = {"transcription": stopping.get_room_metadata("stopping")}
    client, _ = owner_client(room)

    assert client.post(start_url(room)).status_code == 409

    Transcript.objects.filter(pk=stopping.pk).update(
        updated_at=stopping.updated_at - timedelta(seconds=181)
    )
    response = client.post(start_url(room))

    assert response.status_code == 201
    stopping.refresh_from_db()
    assert stopping.status == TranscriptStatusChoices.FAILED
    assert stopping.error == STOPPING_TIMEOUT_ERROR


def test_start_transcription_metadata_of_another_room(transcription_enabled, livekit):
    """A key referring to the transcript of another room is overwritten."""
    room = RoomFactory()
    other_room_transcript = TranscriptFactory(status=TranscriptStatusChoices.ACTIVE)
    livekit.metadata = {
        "transcription": other_room_transcript.get_room_metadata("started")
    }
    client, _ = owner_client(room)

    response = client.post(start_url(room))

    assert response.status_code == 201
    assert livekit.metadata["transcription"]["id"] == response.json()["id"]
    other_room_transcript.refresh_from_db()
    assert other_room_transcript.status == TranscriptStatusChoices.ACTIVE


@pytest.mark.parametrize(
    "entry",
    [
        {"id": "00000000-0000-4000-8000-000000000000", "status": "started"},
        {"id": "not-a-uuid", "status": "stopping"},
        "garbage",
    ],
)
def test_start_transcription_unknown_metadata(transcription_enabled, livekit, entry):
    """A key referring to an unknown transcript is overwritten."""
    room = RoomFactory()
    livekit.metadata = {"transcription": entry}
    client, _ = owner_client(room)

    response = client.post(start_url(room))

    assert response.status_code == 201
    assert livekit.metadata["transcription"]["id"] == response.json()["id"]
    assert uuid.UUID(livekit.metadata["transcription"]["id"])


def test_start_transcription_room_not_live(transcription_enabled, livekit):
    """A transcription cannot start in a room nobody is connected to."""
    livekit.live = False
    room = RoomFactory()
    client, _ = owner_client(room)

    response = client.post(start_url(room))

    assert response.status_code == 409
    assert response.json() == {"error": f"Nobody is connected to room {room.slug}"}
    assert Transcript.objects.count() == 0
    assert livekit.dispatches == []


def test_start_transcription_livekit_unreachable(transcription_enabled, livekit):
    """LiveKit errors while reading the metadata give a 502 and no transcript."""
    livekit.client.room.list_rooms.side_effect = OSError("connection refused")
    room = RoomFactory()
    client, _ = owner_client(room)

    response = client.post(start_url(room))

    assert response.status_code == 502
    assert response.json() == {
        "error": f"Transcription failed to start for room {room.slug}"
    }
    assert Transcript.objects.count() == 0


def test_start_transcription_metadata_update_failure(transcription_enabled, livekit):
    """A metadata write failure fails the transcript and skips the dispatch."""
    livekit.client.room.update_room_metadata.side_effect = TwirpError(
        "internal", "boom", status=500
    )
    room = RoomFactory()
    client, _ = owner_client(room)

    response = client.post(start_url(room))

    assert response.status_code == 502
    transcript = Transcript.objects.get()
    assert transcript.status == TranscriptStatusChoices.FAILED
    assert transcript.error == "Could not update the room metadata."
    assert livekit.dispatches == []


def test_start_transcription_dispatch_failure(transcription_enabled, livekit):
    """A dispatch failure gives a 502, a failed transcript and removes the key."""
    livekit.client.agent_dispatch.create_dispatch.side_effect = TwirpError(
        "unavailable", "no agent", status=503
    )
    livekit.metadata = {"recording_mode": "screen_recording", "access_level": "public"}
    room = RoomFactory()
    client, _ = owner_client(room)

    response = client.post(start_url(room))

    assert response.status_code == 502
    assert response.json() == {
        "error": f"Transcription failed to start for room {room.slug}"
    }
    transcript = Transcript.objects.get()
    assert transcript.status == TranscriptStatusChoices.FAILED
    assert transcript.ended_at is not None
    assert transcript.error == "Could not dispatch the transcription agent."
    assert livekit.metadata == {
        "recording_mode": "screen_recording",
        "access_level": "public",
    }

    # The room is not blocked: a new attempt can start
    livekit.client.agent_dispatch.create_dispatch.side_effect = None
    livekit.client.agent_dispatch.create_dispatch.return_value = mock.Mock(id="AD_2")
    response = client.post(start_url(room))
    assert response.status_code == 201


def test_start_transcription_deleted_room(transcription_enabled, livekit):
    """Soft-deleted rooms cannot be transcribed."""
    room = RoomFactory()
    client, _ = owner_client(room)
    room.soft_delete()

    response = client.post(start_url(room))

    assert response.status_code == 410
    assert Transcript.objects.count() == 0


def test_start_transcription_unknown_room(transcription_enabled, livekit):
    """Unknown rooms give a 404."""
    user = UserFactory()
    client = APIClient()
    client.force_login(user)

    response = client.post(
        "/api/v1.0/rooms/7b8e3a3c-5d2a-4b1a-9c3e-0d6f1e2a3b4c/start-transcription/"
    )

    assert response.status_code == 404
