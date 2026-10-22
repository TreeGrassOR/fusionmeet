"""Test reading the transcript of a call."""

# pylint: disable=unused-argument

import json
import uuid
from datetime import timedelta
from unittest import mock

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from core.factories import RoomFactory, UserFactory
from core.models import RoleChoices

from calls.factories import CallParticipantFactory, CallSessionFactory
from calls.tests.utils import logged_in_client
from transcripts.factories import TranscriptFactory
from transcripts.models import TranscriptStatusChoices

pytestmark = pytest.mark.django_db

DOCUMENT = {
    "version": 1,
    "language": "ru",
    "started_at": "2026-10-22T10:00:00.000Z",
    "ended_at": "2026-10-22T10:30:00.000Z",
    "participants": [{"identity": "user-1", "name": "Иван"}],
    "segments": [
        {
            "identity": "user-1",
            "name": "Иван",
            "start": "2026-10-22T10:00:05.000Z",
            "end": "2026-10-22T10:00:07.000Z",
            "text": "Привет",
        }
    ],
}


def ready_transcript(session, **kwargs):
    """A ready transcript started during a call."""
    transcript_id = uuid.uuid4()
    return TranscriptFactory(
        id=transcript_id,
        room=session.room,
        status=TranscriptStatusChoices.READY,
        started_at=session.started_at + timedelta(minutes=1),
        json_key=f"transcriptions/{transcript_id}/transcript.json",
        md_key=f"transcriptions/{transcript_id}/transcript.md",
        **kwargs,
    )


def transcript_url(session, transcript):
    """URL of the transcript of a call."""
    return f"/api/v1.0/calls/{session.id}/transcripts/{transcript.id}/"


def get_with_document(client, url, content=None):
    """Get a transcript while the storage holds its JSON document."""
    with mock.patch("calls.views.default_storage") as storage:
        storage.open.return_value = ContentFile(
            content if content is not None else json.dumps(DOCUMENT).encode(),
            name="transcript.json",
        )
        return client.get(url), storage


def test_call_transcript_participant():
    """Participants read the transcript of their call (default setting)."""
    user = UserFactory()
    session = CallParticipantFactory(user=user).session
    transcript = ready_transcript(session)

    response, storage = get_with_document(
        logged_in_client(user), transcript_url(session, transcript)
    )

    assert response.status_code == 200
    assert response["Content-Type"] == "application/json"
    assert response.json() == DOCUMENT
    storage.open.assert_called_once_with(transcript.json_key, "rb")


def test_call_transcript_from_storage():
    """The document is read from the S3 storage."""
    user = UserFactory()
    session = CallParticipantFactory(user=user).session
    transcript = ready_transcript(session)
    default_storage.save(
        transcript.json_key, ContentFile(json.dumps(DOCUMENT).encode())
    )

    try:
        response = logged_in_client(user).get(transcript_url(session, transcript))
    finally:
        default_storage.delete(transcript.json_key)

    assert response.status_code == 200
    assert response.json() == DOCUMENT


def test_call_transcript_participants_setting_off(settings):
    """Without the setting, participants not given access are refused."""
    settings.CALLS_TRANSCRIPTS_VISIBLE_TO_PARTICIPANTS = False
    user = UserFactory()
    session = CallParticipantFactory(user=user).session
    transcript = ready_transcript(session)

    response, storage = get_with_document(
        logged_in_client(user), transcript_url(session, transcript)
    )

    assert response.status_code == 403
    storage.open.assert_not_called()


def test_call_transcript_given_access(settings):
    """Users given access to the transcript read it, whatever the setting."""
    settings.CALLS_TRANSCRIPTS_VISIBLE_TO_PARTICIPANTS = False
    user = UserFactory()
    room = RoomFactory(users=[(user, RoleChoices.ADMIN)])
    session = CallParticipantFactory(session__room=room).session
    transcript = ready_transcript(session, users=[user])

    response, _storage = get_with_document(
        logged_in_client(user), transcript_url(session, transcript)
    )

    assert response.status_code == 200
    assert response.json() == DOCUMENT


def test_call_transcript_room_admin_absent_without_access():
    """A room administrator who did not attend needs access to the transcript."""
    user = UserFactory()
    room = RoomFactory(users=[(user, RoleChoices.ADMIN)])
    session = CallParticipantFactory(session__room=room).session
    transcript = ready_transcript(session)

    response, _storage = get_with_document(
        logged_in_client(user), transcript_url(session, transcript)
    )

    assert response.status_code == 403


def test_call_transcript_call_not_visible():
    """The transcript of a call the user cannot see is not found."""
    user = UserFactory()
    session = CallParticipantFactory().session
    transcript = ready_transcript(session, users=[user])

    response, _storage = get_with_document(
        logged_in_client(user), transcript_url(session, transcript)
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    "status", [TranscriptStatusChoices.ACTIVE, TranscriptStatusChoices.STOPPING]
)
def test_call_transcript_not_ready(status):
    """A transcript still being written is not available yet."""
    user = UserFactory()
    session = CallParticipantFactory(user=user).session
    transcript = ready_transcript(session)
    transcript.status = status
    transcript.save()

    response, storage = get_with_document(
        logged_in_client(user), transcript_url(session, transcript)
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "The transcript is not ready."}
    storage.open.assert_not_called()


def test_call_transcript_outside_call():
    """Transcripts of another call of the room are not found."""
    user = UserFactory()
    session = CallSessionFactory(ended_at=timezone.now() - timedelta(minutes=30))
    CallParticipantFactory(session=session, user=user)
    later = ready_transcript(session)
    later.started_at = timezone.now()
    later.save()
    elsewhere = ready_transcript(CallSessionFactory())

    client = logged_in_client(user)
    for transcript in (later, elsewhere):
        response, storage = get_with_document(
            client, transcript_url(session, transcript)
        )
        assert response.status_code == 404
        storage.open.assert_not_called()


def test_call_transcript_unknown():
    """Unknown or malformed transcript ids are not found."""
    user = UserFactory()
    session = CallParticipantFactory(user=user).session
    client = logged_in_client(user)

    for transcript_id in (uuid.uuid4(), "------------------------------------"):
        response = client.get(
            f"/api/v1.0/calls/{session.id}/transcripts/{transcript_id}/"
        )
        assert response.status_code == 404


def test_call_transcript_without_file():
    """A ready transcript without JSON file is not available."""
    user = UserFactory()
    session = CallParticipantFactory(user=user).session
    transcript = ready_transcript(session)
    transcript.json_key = None
    transcript.save()

    response, storage = get_with_document(
        logged_in_client(user), transcript_url(session, transcript)
    )

    assert response.status_code == 404
    storage.open.assert_not_called()


def test_call_transcript_missing_file():
    """A missing S3 object gives a 404."""
    user = UserFactory()
    session = CallParticipantFactory(user=user).session
    transcript = ready_transcript(session)

    with mock.patch("calls.views.default_storage") as storage:
        storage.open.side_effect = FileNotFoundError("missing")
        response = logged_in_client(user).get(transcript_url(session, transcript))

    assert response.status_code == 404


@pytest.mark.parametrize("failure", ["storage", "invalid json"])
def test_call_transcript_unreadable(failure):
    """Storage errors and broken documents give a 502."""
    user = UserFactory()
    session = CallParticipantFactory(user=user).session
    transcript = ready_transcript(session)
    client = logged_in_client(user)

    if failure == "storage":
        with mock.patch("calls.views.default_storage") as storage:
            storage.open.side_effect = OSError("S3 is down")
            response = client.get(transcript_url(session, transcript))
    else:
        response, _storage = get_with_document(
            client, transcript_url(session, transcript), content=b"{not json"
        )

    assert response.status_code == 502
    assert response.json() == {"error": "The transcript file could not be read."}


def test_call_transcript_anonymous():
    """Anonymous users cannot read transcripts."""
    session = CallParticipantFactory().session
    transcript = ready_transcript(session)

    assert APIClient().get(transcript_url(session, transcript)).status_code == 401
