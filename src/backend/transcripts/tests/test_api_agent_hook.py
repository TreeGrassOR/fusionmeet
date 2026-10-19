"""Test the agent hook of the transcripts application."""

# pylint: disable=redefined-outer-name,unused-argument

import uuid
from unittest import mock

from django.core import mail

import pytest
from rest_framework.test import APIClient

from core.factories import RoomFactory, UserFactory

from transcripts.factories import TranscriptFactory
from transcripts.models import TranscriptStatusChoices
from transcripts.tests.utils import AGENT_TOKEN

pytestmark = pytest.mark.django_db

HOOK_URL = "/api/v1.0/transcripts/agent-hook/"


def post_hook(payload, token=AGENT_TOKEN):
    """Post a payload to the agent hook with the given Bearer token."""
    client = APIClient()
    headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
    return client.post(HOOK_URL, payload, format="json", **headers)


def ready_payload(transcript, **overrides):
    """Hook payload of a ready transcript."""
    payload = {
        "transcript_id": str(transcript.id),
        "room_id": str(transcript.room_id),
        "status": "ready",
        "started_at": "2026-10-19T10:00:00.000Z",
        "ended_at": "2026-10-19T11:12:00.500Z",
        "participants": [
            {"identity": "user-1", "name": "Иван Петров"},
            {"identity": "guest-2", "name": ""},
        ],
        "segments_count": 12,
        "files": {
            "json": f"transcriptions/{transcript.id}/transcript.json",
            "md": f"transcriptions/{transcript.id}/transcript.md",
        },
        "error": None,
    }
    payload.update(overrides)
    return payload


@pytest.fixture
def email_mock():
    """Mock the email task used by the hook."""
    with mock.patch("transcripts.services.send_email") as send_email:
        yield send_email.delay


# Authentication


@pytest.mark.parametrize("token_setting", [None, ""])
def test_agent_hook_disabled_without_token(settings, livekit, token_setting):
    """The hook does not exist when no agent token is configured."""
    settings.TRANSCRIPTION_AGENT_TOKEN = token_setting
    transcript = TranscriptFactory()

    response = post_hook(ready_payload(transcript), token="anything")

    assert response.status_code == 404
    transcript.refresh_from_db()
    assert transcript.status == TranscriptStatusChoices.ACTIVE


def test_agent_hook_missing_token(agent_token, livekit):
    """Requests without the Authorization header are rejected."""
    transcript = TranscriptFactory()

    response = post_hook(ready_payload(transcript), token=None)

    assert response.status_code == 401
    transcript.refresh_from_db()
    assert transcript.status == TranscriptStatusChoices.ACTIVE


@pytest.mark.parametrize(
    "authorization",
    ["Bearer wrong-token", f"Token {AGENT_TOKEN}", "Bearer", AGENT_TOKEN],
)
def test_agent_hook_wrong_token(agent_token, livekit, authorization):
    """Requests with a wrong token or scheme are rejected."""
    transcript = TranscriptFactory()

    response = APIClient().post(
        HOOK_URL,
        ready_payload(transcript),
        format="json",
        HTTP_AUTHORIZATION=authorization,
    )

    assert response.status_code == 401
    transcript.refresh_from_db()
    assert transcript.status == TranscriptStatusChoices.ACTIVE


def test_agent_hook_user_session_rejected(agent_token, livekit):
    """A logged-in user cannot call the hook."""
    user = UserFactory()
    transcript = TranscriptFactory(users=[user])
    client = APIClient()
    client.force_login(user)

    response = client.post(HOOK_URL, ready_payload(transcript), format="json")

    assert response.status_code == 401


# Validation


def test_agent_hook_unknown_transcript(agent_token, livekit):
    """Unknown transcripts give a 404 (the agent does not retry it)."""
    response = post_hook(
        ready_payload(TranscriptFactory.build(id=uuid.uuid4()))  # not saved
    )

    assert response.status_code == 404


@pytest.mark.parametrize(
    "overrides",
    [
        {"status": "started"},
        {"transcript_id": "not-a-uuid"},
        {"files": None},
        {"files": {"json": "transcriptions/x/transcript.json"}},
        {"segments_count": -1},
        {"room_id": None},
        {"room_id": ""},
    ],
)
def test_agent_hook_invalid_payload(agent_token, livekit, overrides):
    """Invalid payloads give a 400."""
    transcript = TranscriptFactory()

    response = post_hook(ready_payload(transcript, **overrides))

    assert response.status_code == 400
    transcript.refresh_from_db()
    assert transcript.status == TranscriptStatusChoices.ACTIVE


def test_agent_hook_room_id_required(agent_token, livekit):
    """The agent must tell which LiveKit room the transcript was written in."""
    transcript = TranscriptFactory()
    payload = ready_payload(transcript)
    del payload["room_id"]

    response = post_hook(payload)

    assert response.status_code == 400
    assert "room_id" in response.json()
    transcript.refresh_from_db()
    assert transcript.status == TranscriptStatusChoices.ACTIVE


@pytest.mark.parametrize("room_id", ["other_room", "not-a-room", "uppercase"])
def test_agent_hook_room_mismatch(agent_token, livekit, email_mock, room_id):
    """A result posted from another room is rejected before any side effect."""
    transcript = TranscriptFactory(status=TranscriptStatusChoices.STOPPING)
    metadata = {"transcription": transcript.get_room_metadata("stopping")}
    livekit.metadata = metadata
    room_id = {
        "other_room": str(RoomFactory().id),
        "not-a-room": "not-a-room",
        "uppercase": str(transcript.room_id).upper(),
    }[room_id]

    response = post_hook(ready_payload(transcript, room_id=room_id))

    assert response.status_code == 400
    assert response.json() == {
        "room_id": "The transcript does not belong to this room."
    }
    transcript.refresh_from_db()
    assert transcript.status == TranscriptStatusChoices.STOPPING
    assert transcript.md_key is None
    assert livekit.metadata == metadata
    livekit.client.room.update_room_metadata.assert_not_called()
    email_mock.assert_not_called()


def test_agent_hook_values_over_model_limits(agent_token, livekit):
    """Values the model would reject give a 400 before any side effect."""
    transcript = TranscriptFactory()
    metadata = {"transcription": transcript.get_room_metadata("stopping")}
    livekit.metadata = metadata
    long_key = f"transcriptions/{transcript.id}/{'a' * 255}.md"

    for overrides in [
        {"files": {"json": f"transcriptions/{transcript.id}/t.json", "md": long_key}},
        {"segments_count": 2**31},
    ]:
        response = post_hook(ready_payload(transcript, **overrides))

        assert response.status_code == 400, overrides

    transcript.refresh_from_db()
    assert transcript.status == TranscriptStatusChoices.ACTIVE
    assert livekit.metadata == metadata
    livekit.client.room.update_room_metadata.assert_not_called()


def test_agent_hook_participant_without_name(agent_token, livekit, email_mock):
    """A participant name sent as null is stored as an empty name."""
    transcript = TranscriptFactory()

    response = post_hook(
        ready_payload(transcript, participants=[{"identity": "u1", "name": None}])
    )

    assert response.status_code == 200
    transcript.refresh_from_db()
    assert transcript.participants == [{"identity": "u1", "name": ""}]


@pytest.mark.parametrize(
    "key",
    [
        "recordings/{other}.mp4",
        "transcriptions/{other}/transcript.md",
        "transcriptions/{id}/../{other}/transcript.md",
    ],
)
def test_agent_hook_key_outside_prefix(agent_token, livekit, key):
    """Keys outside of the transcript prefix are rejected."""
    transcript = TranscriptFactory()
    files = {
        "json": f"transcriptions/{transcript.id}/transcript.json",
        "md": key.format(id=transcript.id, other=uuid.uuid4()),
    }

    response = post_hook(ready_payload(transcript, files=files))

    assert response.status_code == 400
    assert "files" in response.json()
    transcript.refresh_from_db()
    assert transcript.status == TranscriptStatusChoices.ACTIVE
    assert transcript.md_key is None


# Ready / failed


@pytest.mark.parametrize(
    "status",
    [
        TranscriptStatusChoices.ACTIVE,
        TranscriptStatusChoices.STOPPING,
        TranscriptStatusChoices.FAILED,
    ],
)
def test_agent_hook_ready(agent_token, livekit, email_mock, status):
    """A ready result is applied whatever the ongoing or failed status."""
    transcript = TranscriptFactory(status=status, error="previous error")
    livekit.metadata = {
        "recording_mode": "screen_recording",
        "transcription": transcript.get_room_metadata("stopping"),
    }

    response = post_hook(ready_payload(transcript))

    assert response.status_code == 200
    assert response.json() == {"id": str(transcript.id), "status": "ready"}
    transcript.refresh_from_db()
    assert transcript.status == TranscriptStatusChoices.READY
    assert transcript.started_at.isoformat() == "2026-10-19T10:00:00+00:00"
    assert transcript.ended_at.isoformat() == "2026-10-19T11:12:00.500000+00:00"
    assert transcript.participants == [
        {"identity": "user-1", "name": "Иван Петров"},
        {"identity": "guest-2", "name": ""},
    ]
    assert transcript.segments_count == 12
    assert transcript.json_key == f"transcriptions/{transcript.id}/transcript.json"
    assert transcript.md_key == f"transcriptions/{transcript.id}/transcript.md"
    assert transcript.error == ""
    # The metadata key is removed, the other keys are kept
    assert livekit.metadata == {"recording_mode": "screen_recording"}


def test_agent_hook_ready_idempotent(agent_token, livekit, email_mock, settings):
    """Repeating a ready result keeps the transcript ready and sends one email."""
    settings.EMAIL_HOST = "smtp.example.com"
    transcript = TranscriptFactory(started_by=UserFactory())

    assert post_hook(ready_payload(transcript)).status_code == 200
    response = post_hook(ready_payload(transcript, segments_count=13))

    assert response.status_code == 200
    transcript.refresh_from_db()
    assert transcript.status == TranscriptStatusChoices.READY
    assert transcript.segments_count == 13
    assert email_mock.call_count == 1


def test_agent_hook_metadata_of_another_transcript(agent_token, livekit, email_mock):
    """The metadata key is kept when it refers to another transcript."""
    room = RoomFactory()
    old = TranscriptFactory(room=room, status=TranscriptStatusChoices.FAILED)
    current = TranscriptFactory(room=room, status=TranscriptStatusChoices.ACTIVE)
    livekit.metadata = {"transcription": current.get_room_metadata("started")}

    response = post_hook(ready_payload(old))

    assert response.status_code == 200
    old.refresh_from_db()
    assert old.status == TranscriptStatusChoices.READY
    assert livekit.metadata == {"transcription": current.get_room_metadata("started")}
    livekit.client.room.update_room_metadata.assert_not_called()


def test_agent_hook_room_not_live(agent_token, livekit, email_mock):
    """The result is applied when the call is over (no LiveKit room)."""
    livekit.live = False
    transcript = TranscriptFactory(status=TranscriptStatusChoices.STOPPING)

    response = post_hook(ready_payload(transcript))

    assert response.status_code == 200
    transcript.refresh_from_db()
    assert transcript.status == TranscriptStatusChoices.READY


def test_agent_hook_livekit_error(agent_token, livekit, email_mock):
    """LiveKit errors while removing the metadata do not fail the hook."""
    livekit.client.room.list_rooms.side_effect = OSError("LiveKit is down")
    transcript = TranscriptFactory()

    response = post_hook(ready_payload(transcript))

    assert response.status_code == 200
    transcript.refresh_from_db()
    assert transcript.status == TranscriptStatusChoices.READY


@pytest.mark.parametrize(
    "status", [TranscriptStatusChoices.ACTIVE, TranscriptStatusChoices.STOPPING]
)
def test_agent_hook_failed(agent_token, livekit, email_mock, settings, status):
    """A failed result is stored with its error and sends no email."""
    settings.EMAIL_HOST = "smtp.example.com"
    transcript = TranscriptFactory(status=status, started_by=UserFactory())
    livekit.metadata = {"transcription": transcript.get_room_metadata("started")}

    response = post_hook(
        {
            "transcript_id": str(transcript.id),
            "room_id": str(transcript.room_id),
            "status": "failed",
            "started_at": "2026-10-19T10:00:00.000Z",
            "ended_at": "2026-10-19T10:30:00.000Z",
            "participants": [],
            "segments_count": 0,
            "files": {"json": None, "md": None},
            "error": "ClientError: access denied",
        }
    )

    assert response.status_code == 200
    assert response.json() == {"id": str(transcript.id), "status": "failed"}
    transcript.refresh_from_db()
    assert transcript.status == TranscriptStatusChoices.FAILED
    assert transcript.error == "ClientError: access denied"
    assert transcript.json_key is None
    assert transcript.md_key is None
    assert livekit.metadata == {}
    email_mock.assert_not_called()


def test_agent_hook_failed_minimal_payload(agent_token, livekit, email_mock):
    """A failure with only the required fields is accepted, twice."""
    transcript = TranscriptFactory()
    payload = {
        "transcript_id": str(transcript.id),
        "room_id": str(transcript.room_id),
        "status": "failed",
    }

    assert post_hook(payload).status_code == 200
    assert post_hook(payload).status_code == 200

    transcript.refresh_from_db()
    assert transcript.status == TranscriptStatusChoices.FAILED
    assert transcript.error == "Transcription failed."
    assert transcript.ended_at is not None


def test_agent_hook_failed_after_ready_ignored(agent_token, livekit, email_mock):
    """A failure never overrides a ready transcript."""
    transcript = TranscriptFactory()
    assert post_hook(ready_payload(transcript)).status_code == 200

    response = post_hook(
        {
            "transcript_id": str(transcript.id),
            "room_id": str(transcript.room_id),
            "status": "failed",
            "error": "late",
        }
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ready"
    transcript.refresh_from_db()
    assert transcript.status == TranscriptStatusChoices.READY
    assert transcript.error == ""
    assert transcript.md_key is not None


# Email


def test_agent_hook_email_only_to_initiator(agent_token, livekit, email_mock, settings):
    """Only the initiator is emailed, with a link to the transcripts page."""
    settings.EMAIL_HOST = "smtp.example.com"
    settings.EMAIL_APP_BASE_URL = "https://meet.example.com/"
    settings.TRANSCRIPTION_LIST_URL = None
    room = RoomFactory(name="Daily")
    initiator = UserFactory(language="fr-fr")
    owner = UserFactory()
    transcript = TranscriptFactory(
        room=room, started_by=initiator, users=[initiator, owner]
    )

    response = post_hook(ready_payload(transcript))

    assert response.status_code == 200
    email_mock.assert_called_once()
    kwargs = email_mock.call_args.kwargs
    assert kwargs["template"] == "transcript_ready"
    assert kwargs["subject"] == "Your transcript is ready"
    assert kwargs["recipients"] == [initiator.email]
    assert kwargs["language"] == "fr-fr"
    assert kwargs["context"]["room_name"] == "Daily"
    assert kwargs["context"]["link"] == "https://meet.example.com/my-recordings"


def test_agent_hook_email_custom_list_url(agent_token, livekit, email_mock, settings):
    """TRANSCRIPTION_LIST_URL overrides the link of the email."""
    settings.EMAIL_HOST = "smtp.example.com"
    settings.TRANSCRIPTION_LIST_URL = "https://meet.example.com/custom"
    transcript = TranscriptFactory(started_by=UserFactory())

    post_hook(ready_payload(transcript))

    assert email_mock.call_args.kwargs["context"]["link"] == (
        "https://meet.example.com/custom"
    )


@pytest.mark.parametrize("email_host", [None, ""])
def test_agent_hook_no_email_without_smtp(
    agent_token, livekit, email_mock, settings, email_host
):
    """No email is sent when SMTP is not configured."""
    settings.EMAIL_HOST = email_host
    transcript = TranscriptFactory(started_by=UserFactory())

    response = post_hook(ready_payload(transcript))

    assert response.status_code == 200
    transcript.refresh_from_db()
    assert transcript.status == TranscriptStatusChoices.READY
    email_mock.assert_not_called()


def test_agent_hook_no_email_without_initiator(
    agent_token, livekit, email_mock, settings
):
    """No email is sent when the initiator account is gone."""
    settings.EMAIL_HOST = "smtp.example.com"
    transcript = TranscriptFactory(started_by=None)

    response = post_hook(ready_payload(transcript))

    assert response.status_code == 200
    email_mock.assert_not_called()


@pytest.mark.parametrize(
    "error", [ConnectionRefusedError("refused"), TimeoutError(), Exception("boom")]
)
def test_agent_hook_email_error(agent_token, livekit, email_mock, settings, error):
    """Email errors are logged and never fail the hook."""
    settings.EMAIL_HOST = "smtp.example.com"
    email_mock.side_effect = error
    transcript = TranscriptFactory(started_by=UserFactory())

    response = post_hook(ready_payload(transcript))

    assert response.status_code == 200
    transcript.refresh_from_db()
    assert transcript.status == TranscriptStatusChoices.READY


def test_agent_hook_email_rendered(agent_token, livekit, settings):
    """The transcript_ready template is rendered and sent to the initiator only."""
    settings.EMAIL_HOST = "smtp.example.com"
    settings.EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
    settings.EMAIL_APP_BASE_URL = "https://meet.example.com"
    settings.TRANSCRIPTION_LIST_URL = None
    initiator = UserFactory(language="en-us")
    room = RoomFactory(name="Board meeting")
    transcript = TranscriptFactory(
        room=room, started_by=initiator, users=[initiator, UserFactory()]
    )

    response = post_hook(ready_payload(transcript))

    assert response.status_code == 200
    assert len(mail.outbox) == 1
    email = mail.outbox[0]
    assert email.to == [initiator.email]
    assert email.subject == "Your transcript is ready"
    assert "Board meeting" in email.body
    assert "https://meet.example.com/my-recordings" in email.body
    html = email.alternatives[0][0]
    assert 'href="https://meet.example.com/my-recordings"' in html
