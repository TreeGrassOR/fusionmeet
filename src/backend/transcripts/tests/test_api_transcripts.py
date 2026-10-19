"""Test the transcripts list, retrieve and download endpoints."""

# pylint: disable=redefined-outer-name,unused-argument

import uuid
from datetime import datetime, timezone
from unittest import mock
from zoneinfo import ZoneInfo

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

import pytest
from rest_framework.test import APIClient

from core.factories import RoomFactory, UserFactory

from transcripts.factories import TranscriptFactory
from transcripts.models import TranscriptStatusChoices

pytestmark = pytest.mark.django_db


def logged_in_client(user):
    """Return an API client logged in as the given user."""
    client = APIClient()
    client.force_login(user)
    return client


def ready_transcript(**kwargs):
    """Create a ready transcript whose file keys are under its prefix."""
    transcript_id = uuid.uuid4()
    return TranscriptFactory(
        id=transcript_id,
        status=TranscriptStatusChoices.READY,
        json_key=f"transcriptions/{transcript_id}/transcript.json",
        md_key=f"transcriptions/{transcript_id}/transcript.md",
        **kwargs,
    )


# List


def test_list_transcripts_anonymous():
    """Anonymous users cannot list transcripts."""
    TranscriptFactory()

    response = APIClient().get("/api/v1.0/transcripts/")

    assert response.status_code == 401


def test_list_transcripts_only_own(transcription_enabled):
    """Users only see the transcripts they were given access to, newest first."""
    user = UserFactory()
    other = UserFactory()
    older = ready_transcript(users=[user, other])
    newer = TranscriptFactory(users=[user])
    TranscriptFactory(users=[other])
    TranscriptFactory()

    response = logged_in_client(user).get("/api/v1.0/transcripts/")

    assert response.status_code == 200
    content = response.json()
    assert content["count"] == 2
    assert content["next"] is None
    assert [item["id"] for item in content["results"]] == [
        str(newer.id),
        str(older.id),
    ]
    assert content["results"][1]["downloads"] == {
        "md": f"/api/v1.0/transcripts/{older.id}/download/?format=md",
        "json": f"/api/v1.0/transcripts/{older.id}/download/?format=json",
    }
    assert content["results"][1]["is_ready"] is True
    assert content["results"][0]["downloads"] == {"md": None, "json": None}
    assert content["results"][0]["is_ready"] is False


def test_list_transcripts_works_when_feature_disabled(settings):
    """Existing transcripts stay listed when the feature is turned off."""
    settings.TRANSCRIPTION_ENABLED = False
    user = UserFactory()
    TranscriptFactory(users=[user])

    response = logged_in_client(user).get("/api/v1.0/transcripts/")

    assert response.status_code == 200
    assert response.json()["count"] == 1


def test_list_transcripts_pagination():
    """The list is paginated like the recordings."""
    user = UserFactory()
    for _ in range(3):
        TranscriptFactory(users=[user])

    response = logged_in_client(user).get("/api/v1.0/transcripts/?page_size=2")

    assert response.status_code == 200
    assert response.json()["count"] == 3
    assert len(response.json()["results"]) == 2
    assert response.json()["next"] is not None


# Retrieve


def test_retrieve_transcript_without_access():
    """Users without access get a 404."""
    transcript = TranscriptFactory(users=[UserFactory()])

    response = logged_in_client(UserFactory()).get(
        f"/api/v1.0/transcripts/{transcript.id}/"
    )

    assert response.status_code == 404


def test_retrieve_transcript_with_access():
    """Users with access get the transcript."""
    user = UserFactory()
    room = RoomFactory(name="Weekly")
    transcript = ready_transcript(
        room=room,
        users=[user],
        participants=[{"identity": "u1", "name": "Ivan"}],
        segments_count=3,
    )

    response = logged_in_client(user).get(f"/api/v1.0/transcripts/{transcript.id}/")

    assert response.status_code == 200
    content = response.json()
    assert content["room"] == {"id": str(room.id), "name": "Weekly", "slug": room.slug}
    assert content["status"] == "ready"
    assert content["participants"] == [{"identity": "u1", "name": "Ivan"}]
    assert content["segments_count"] == 3


def test_retrieve_transcript_invalid_keys_have_no_download():
    """Keys outside of the transcript prefix are never exposed as downloads."""
    user = UserFactory()
    transcript = TranscriptFactory(
        users=[user],
        status=TranscriptStatusChoices.READY,
        md_key="recordings/secret.mp4",
        json_key=None,
    )

    response = logged_in_client(user).get(f"/api/v1.0/transcripts/{transcript.id}/")

    assert response.json()["downloads"] == {"md": None, "json": None}


# Download


def test_download_transcript_anonymous():
    """Anonymous users cannot download transcripts."""
    transcript = ready_transcript()

    response = APIClient().get(f"/api/v1.0/transcripts/{transcript.id}/download/")

    assert response.status_code == 401


def test_download_transcript_without_access():
    """Users without access get a 404."""
    transcript = ready_transcript(users=[UserFactory()])

    response = logged_in_client(UserFactory()).get(
        f"/api/v1.0/transcripts/{transcript.id}/download/?format=md"
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    "status",
    [
        TranscriptStatusChoices.ACTIVE,
        TranscriptStatusChoices.STOPPING,
        TranscriptStatusChoices.FAILED,
    ],
)
def test_download_transcript_not_ready(status):
    """Only ready transcripts can be downloaded."""
    user = UserFactory()
    transcript_id = uuid.uuid4()
    transcript = TranscriptFactory(
        id=transcript_id,
        users=[user],
        status=status,
        md_key=f"transcriptions/{transcript_id}/transcript.md",
    )

    with mock.patch("transcripts.views.default_storage") as storage:
        response = logged_in_client(user).get(
            f"/api/v1.0/transcripts/{transcript.id}/download/?format=md"
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "The transcript is not ready."}
    storage.open.assert_not_called()


def test_download_transcript_invalid_format():
    """Unknown formats are rejected."""
    user = UserFactory()
    transcript = ready_transcript(users=[user])

    response = logged_in_client(user).get(
        f"/api/v1.0/transcripts/{transcript.id}/download/?format=mp4"
    )

    assert response.status_code == 400
    assert response.json() == {"format": "Must be one of: md, json."}


@pytest.mark.parametrize(
    "key_template",
    [
        "recordings/{id}.mp4",
        "transcriptions/{other}/transcript.md",
        "transcriptions/{id}/../../recordings/{other}.mp4",
        "transcriptions/{id}/./transcript.md",
        "transcriptions/{id}//transcript.md",
        "transcriptions/{id}/",
        "transcriptions/{id}\\..\\..\\recordings\\x.mp4",
        "transcriptions/{id}transcript.md",
        "/transcriptions/{id}/transcript.md",
    ],
)
def test_download_transcript_key_outside_prefix(key_template):
    """Stored keys that are not under the transcript prefix are never read."""
    user = UserFactory()
    transcript_id = uuid.uuid4()
    transcript = TranscriptFactory(
        id=transcript_id,
        users=[user],
        status=TranscriptStatusChoices.READY,
        md_key=key_template.format(id=transcript_id, other=uuid.uuid4()),
    )

    with mock.patch("transcripts.views.default_storage") as storage:
        response = logged_in_client(user).get(
            f"/api/v1.0/transcripts/{transcript.id}/download/?format=md"
        )

    assert response.status_code == 404
    assert response.json() == {"detail": "The transcript file is not available."}
    storage.open.assert_not_called()


def test_download_transcript_missing_file():
    """A missing S3 object gives a 404."""
    user = UserFactory()
    transcript = ready_transcript(users=[user])

    with mock.patch("transcripts.views.default_storage") as storage:
        storage.open.side_effect = FileNotFoundError("missing")
        response = logged_in_client(user).get(
            f"/api/v1.0/transcripts/{transcript.id}/download/?format=json"
        )

    assert response.status_code == 404
    storage.open.assert_called_once_with(transcript.json_key, "rb")


def test_download_transcript_storage_error():
    """Other storage errors give a 502."""
    user = UserFactory()
    transcript = ready_transcript(users=[user])

    with mock.patch("transcripts.views.default_storage") as storage:
        storage.open.side_effect = OSError("S3 is down")
        response = logged_in_client(user).get(
            f"/api/v1.0/transcripts/{transcript.id}/download/?format=md"
        )

    assert response.status_code == 502
    assert response.json() == {"error": "The transcript file could not be read."}


@pytest.mark.parametrize(
    "file_format, query, content_type, content",
    [
        (
            "md",
            "?format=md",
            "text/markdown; charset=utf-8",
            "# Расшифровка встречи\n\n[00:00:05] **Иван**: привет\n".encode(),
        ),
        ("md", "", "text/markdown; charset=utf-8", b"# Default format\n"),
        (
            "json",
            "?format=json",
            "application/json",
            '{"version": 1, "segments": [{"text": "привет"}]}'.encode(),
        ),
    ],
)
def test_download_transcript_success(file_format, query, content_type, content):
    """Ready transcripts are streamed from the S3 storage as attachments."""
    user = UserFactory(timezone=ZoneInfo("Europe/Moscow"))
    room = RoomFactory(name="Daily sync", slug="daily-sync")
    transcript = ready_transcript(
        room=room,
        users=[user],
        started_at=datetime(2026, 10, 19, 10, 5, tzinfo=timezone.utc),
    )
    key = transcript.get_file_key(file_format)
    default_storage.save(key, ContentFile(content))

    try:
        response = logged_in_client(user).get(
            f"/api/v1.0/transcripts/{transcript.id}/download/{query}"
        )

        assert response.status_code == 200
        assert response["Content-Type"] == content_type
        assert response["Content-Disposition"] == (
            f'attachment; filename="transcript-daily-sync-2026-10-19-1305.{file_format}"'
        )
        assert b"".join(response.streaming_content) == content
    finally:
        default_storage.delete(key)


def test_download_transcript_non_ascii_filename():
    """Non-ASCII room names are encoded with RFC 5987 in the file name."""
    user = UserFactory()
    room = RoomFactory(name="Планёрка", slug="")
    transcript = ready_transcript(
        room=room,
        users=[user],
        started_at=datetime(2026, 10, 19, 10, 5, tzinfo=timezone.utc),
    )

    with mock.patch("transcripts.views.default_storage") as storage:
        storage.open.return_value = ContentFile(b"# test", name="transcript.md")
        response = logged_in_client(user).get(
            f"/api/v1.0/transcripts/{transcript.id}/download/?format=md"
        )

    assert response.status_code == 200
    assert response["Content-Disposition"] == (
        "attachment; filename*=utf-8''transcript-"
        "%D0%BF%D0%BB%D0%B0%D0%BD%D1%91%D1%80%D0%BA%D0%B0-2026-10-19-1005.md"
    )
