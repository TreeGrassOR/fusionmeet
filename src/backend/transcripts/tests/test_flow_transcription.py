"""End-to-end flow of a transcription: start, stop, agent hook, list, download."""

# pylint: disable=unused-argument

from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

import pytest
from rest_framework.test import APIClient

from core.factories import RoomFactory, UserFactory
from core.models import RoleChoices

from transcripts.models import Transcript, TranscriptStatusChoices
from transcripts.tests.utils import AGENT_TOKEN

pytestmark = pytest.mark.django_db


def test_transcription_flow(transcription_enabled, agent_token, livekit):
    """The transcript started by the owner is downloadable once the agent reports it."""
    room = RoomFactory(name="Retro")
    owner = UserFactory()
    room.accesses.create(user=owner, role=RoleChoices.OWNER)
    livekit.metadata = {"access_level": room.access_level}
    client = APIClient()
    client.force_login(owner)

    # The owner starts the transcription
    response = client.post(f"/api/v1.0/rooms/{room.id}/start-transcription/")
    assert response.status_code == 201
    transcript_id = response.json()["id"]
    assert livekit.metadata["transcription"]["status"] == "started"

    # A second start is refused while it runs
    response = client.post(f"/api/v1.0/rooms/{room.id}/start-transcription/")
    assert response.status_code == 409

    # The owner stops it: the agent sees "stopping" in the room metadata
    response = client.post(f"/api/v1.0/rooms/{room.id}/stop-transcription/")
    assert response.status_code == 200
    assert livekit.metadata["transcription"]["id"] == transcript_id
    assert livekit.metadata["transcription"]["status"] == "stopping"

    # The agent writes the files and calls the hook
    md_key = f"transcriptions/{transcript_id}/transcript.md"
    json_key = f"transcriptions/{transcript_id}/transcript.json"
    default_storage.save(
        md_key, ContentFile("# Расшифровка встречи «Retro»\n".encode())
    )
    default_storage.save(json_key, ContentFile(b'{"version": 1}'))
    try:
        response = APIClient().post(
            "/api/v1.0/transcripts/agent-hook/",
            {
                "transcript_id": transcript_id,
                "room_id": str(room.id),
                "status": "ready",
                "started_at": livekit.metadata["transcription"]["started_at"],
                "ended_at": "2030-01-01T00:00:00.000Z",
                "participants": [{"identity": str(owner.sub), "name": "Owner"}],
                "segments_count": 1,
                "files": {"json": json_key, "md": md_key},
                "error": None,
            },
            format="json",
            HTTP_AUTHORIZATION=f"Bearer {AGENT_TOKEN}",
        )
        assert response.status_code == 200
        assert "transcription" not in livekit.metadata
        assert Transcript.objects.get().status == TranscriptStatusChoices.READY

        # The owner lists and downloads it
        response = client.get("/api/v1.0/transcripts/")
        assert response.status_code == 200
        [item] = response.json()["results"]
        assert item["id"] == transcript_id
        assert item["participants"] == [{"identity": str(owner.sub), "name": "Owner"}]

        response = client.get(item["downloads"]["md"])
        assert response.status_code == 200
        assert b"".join(response.streaming_content).decode() == (
            "# Расшифровка встречи «Retro»\n"
        )
        response = client.get(item["downloads"]["json"])
        assert response.status_code == 200
        assert response["Content-Type"] == "application/json"

        # A new transcription can start in the same room
        response = client.post(f"/api/v1.0/rooms/{room.id}/start-transcription/")
        assert response.status_code == 201
    finally:
        default_storage.delete(md_key)
        default_storage.delete(json_key)
