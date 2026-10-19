"""Test the Transcript model."""

import hashlib
import hmac
import uuid
from datetime import datetime, timezone

from django.core.exceptions import ValidationError

import pytest
from rest_framework.test import APIClient

from core.factories import RoomFactory, UserFactory
from core.models import RoleChoices

from transcripts.factories import TranscriptFactory
from transcripts.models import (
    Transcript,
    TranscriptStatusChoices,
    format_timestamp,
    sign_room_transcript,
)

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize(
    "status", [TranscriptStatusChoices.ACTIVE, TranscriptStatusChoices.STOPPING]
)
def test_models_transcript_one_ongoing_per_room(status):
    """A room has at most one ongoing transcript."""
    room = RoomFactory()
    TranscriptFactory(room=room, status=status)

    with pytest.raises(ValidationError):
        TranscriptFactory(room=room, status=TranscriptStatusChoices.ACTIVE)

    # Completed transcripts and other rooms are not concerned
    TranscriptFactory(room=room, status=TranscriptStatusChoices.READY)
    TranscriptFactory(room=room, status=TranscriptStatusChoices.FAILED)
    TranscriptFactory(status=TranscriptStatusChoices.ACTIVE)


def test_models_transcript_room_cascade():
    """Transcripts are deleted with their room."""
    transcript = TranscriptFactory()
    room = transcript.room

    room.delete()

    assert Transcript.objects.count() == 0


def test_models_transcript_grant_initial_accesses():
    """The starter and the room owners/administrators get access."""
    room = RoomFactory()
    owner = UserFactory()
    admin = UserFactory()
    member = UserFactory()
    room.accesses.create(user=owner, role=RoleChoices.OWNER)
    room.accesses.create(user=admin, role=RoleChoices.ADMIN)
    room.accesses.create(user=member, role=RoleChoices.MEMBER)
    starter = UserFactory()
    transcript = TranscriptFactory(room=room, started_by=starter)

    transcript.grant_initial_accesses(starter=starter)

    assert set(transcript.users.all()) == {starter, owner, admin}
    assert list(starter.transcripts.all()) == [transcript]


def test_models_transcript_started_by_deleted():
    """Deleting the starter keeps the transcript."""
    starter = UserFactory()
    transcript = TranscriptFactory(started_by=starter)

    starter.delete()

    transcript.refresh_from_db()
    assert transcript.started_by is None


def test_models_transcript_room_metadata(settings):
    """The room metadata value describes the transcription and is signed."""
    settings.TRANSCRIPTION_AGENT_TOKEN = "agent-token"
    starter = UserFactory(full_name="Мария Соколова")
    room = RoomFactory(name="Daily")
    transcript = TranscriptFactory(
        room=room,
        started_by=starter,
        started_at=datetime(2026, 10, 19, 10, 0, 0, 123456, tzinfo=timezone.utc),
    )

    assert transcript.get_room_metadata("started") == {
        "id": str(transcript.id),
        "status": "started",
        "room_name": "Daily",
        "started_at": "2026-10-19T10:00:00.123Z",
        "started_by": "Мария Соколова",
        "sig": hmac.new(
            b"agent-token",
            f"{room.id}:{transcript.id}".encode(),
            hashlib.sha256,
        ).hexdigest(),
    }

    transcript.started_by = None
    assert transcript.get_room_metadata("stopping")["started_by"] == ""


def test_models_sign_room_transcript(settings):
    """The signature binds the transcript id to the room, with the agent token."""
    settings.TRANSCRIPTION_AGENT_TOKEN = "agent-token"
    room_id, transcript_id = str(uuid.uuid4()), uuid.uuid4()

    signature = sign_room_transcript(room_id, transcript_id)

    message = f"{room_id}:{transcript_id}".encode()
    assert signature == hmac.new(b"agent-token", message, hashlib.sha256).hexdigest()
    assert len(signature) == 64
    assert sign_room_transcript(room_id, str(transcript_id)) == signature
    assert sign_room_transcript(str(uuid.uuid4()), transcript_id) != signature
    assert sign_room_transcript(room_id, uuid.uuid4()) != signature

    settings.TRANSCRIPTION_AGENT_TOKEN = "another-token"
    assert sign_room_transcript(room_id, transcript_id) != signature


# Known answer shared with src/agents/tests/test_transcription_control.py: the
# agent verifies the signature with its own implementation.
SIGNATURE_TOKEN = "agent-token"
SIGNATURE_ROOM_ID = "0f8c2a6e-3b1d-4f5e-9a7b-1c2d3e4f5a6b"
SIGNATURE_TRANSCRIPT_ID = "7c7e3b5c-1d7a-4b8e-9a51-0e1f2a3b4c5d"
SIGNATURE = "896234be7c7ff4d8ffc32a586a8a179142f06a58e5781c671c4008b54bda0eaa"


def test_models_room_metadata_signature_known_answer(settings):
    """The metadata of a saved transcript carries the signature the agent expects."""
    settings.TRANSCRIPTION_AGENT_TOKEN = SIGNATURE_TOKEN
    room = RoomFactory(id=uuid.UUID(SIGNATURE_ROOM_ID))
    transcript = TranscriptFactory(id=uuid.UUID(SIGNATURE_TRANSCRIPT_ID), room=room)
    transcript.refresh_from_db()

    assert str(transcript.room_id) == SIGNATURE_ROOM_ID
    assert transcript.get_room_metadata("started")["sig"] == SIGNATURE
    assert sign_room_transcript(SIGNATURE_ROOM_ID, SIGNATURE_TRANSCRIPT_ID) == SIGNATURE


def test_models_transcript_format_timestamp():
    """Timestamps are formatted in UTC with milliseconds."""
    moment = datetime(2026, 10, 19, 13, 0, 5, 7000, tzinfo=timezone.utc)

    assert format_timestamp(moment) == "2026-10-19T13:00:05.007Z"
    assert format_timestamp(None) is None


@pytest.mark.parametrize(
    "key, expected",
    [
        ("transcriptions/{id}/transcript.md", True),
        ("transcriptions/{id}/transcript.json", True),
        ("transcriptions/{id}/parts/1760868000000-000001.json", True),
        ("transcriptions/{id}/", False),
        ("transcriptions/{id}", False),
        ("transcriptions/{id}transcript.md", False),
        ("transcriptions/{other}/transcript.md", False),
        ("recordings/{id}.mp4", False),
        ("transcriptions/{id}/../{other}/transcript.md", False),
        ("transcriptions/{id}/./transcript.md", False),
        ("transcriptions/{id}/..", False),
        ("transcriptions/{id}//transcript.md", False),
        ("transcriptions/{id}/a b.md", False),
        ("transcriptions/{id}/a\\b.md", False),
        ("/transcriptions/{id}/transcript.md", False),
        (None, False),
        (42, False),
    ],
)
def test_models_transcript_owns_key(settings, key, expected):
    """Only keys under the transcript prefix, without dot segments, are valid."""
    settings.TRANSCRIPTION_S3_PREFIX = "transcriptions"
    transcript = TranscriptFactory()
    if isinstance(key, str):
        key = key.format(id=transcript.id, other=uuid.uuid4())

    assert transcript.owns_key(key) is expected


@pytest.mark.parametrize("prefix", ["custom/prefix", "/custom/prefix/"])
def test_models_transcript_custom_prefix(settings, prefix):
    """The prefix follows TRANSCRIPTION_S3_PREFIX."""
    settings.TRANSCRIPTION_S3_PREFIX = prefix
    transcript = TranscriptFactory()

    assert transcript.key_prefix == f"custom/prefix/{transcript.id}/"
    assert transcript.owns_key(f"custom/prefix/{transcript.id}/transcript.md")
    assert not transcript.owns_key(f"transcriptions/{transcript.id}/transcript.md")


@pytest.mark.parametrize("enabled", [True, False])
def test_api_config_exposes_transcription(settings, enabled):
    """The frontend configuration tells whether transcription is enabled."""
    settings.TRANSCRIPTION_ENABLED = enabled

    response = APIClient().get("/api/v1.0/config/")

    assert response.status_code == 200
    assert response.json()["transcription"] == {"enabled": enabled}
