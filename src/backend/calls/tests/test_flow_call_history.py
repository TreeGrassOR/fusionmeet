"""A call from start to end, as LiveKit and the clients report it."""

import json
from datetime import timedelta
from unittest import mock

from django.contrib.auth.models import AnonymousUser
from django.core.files.base import ContentFile
from django.utils import timezone

import pytest

from core.factories import RoomFactory, UserFactory
from core.models import RoleChoices
from core.utils import generate_token

from calls.tests.utils import (
    epoch_milliseconds,
    livekit_client,
    livekit_token,
    logged_in_client,
    participant_info,
    post_webhook,
    webhook_event,
)
from transcripts.factories import TranscriptFactory
from transcripts.models import TranscriptStatusChoices

pytestmark = pytest.mark.django_db


def test_flow_call_history():
    """Two users and a guest talk in a call that is transcribed: afterwards both
    users find the call with its chat and transcript.
    """
    owner = UserFactory(full_name="Иван Петров")
    colleague = UserFactory(full_name="Мария Смирнова")
    room = RoomFactory(name="abc-defg-hij", users=[(owner, RoleChoices.OWNER)])
    start = timezone.now().replace(microsecond=0) - timedelta(minutes=40)
    sid = "RM_flow"

    def hook(event, at, **kwargs):
        response = post_webhook(
            webhook_event(
                event, room.id, sid=sid, at=at, room_created_at=start, **kwargs
            )
        )
        assert response.status_code == 200

    hook("room_started", start)
    people = [
        (owner.sub, "Иван Петров", True),
        (colleague.sub, "Мария Смирнова", True),
        ("guest-identity", "Гость", False),
    ]
    for index, (identity, name, is_authenticated) in enumerate(people):
        joined_at = start + timedelta(seconds=10 * (index + 1))
        hook(
            "participant_joined",
            joined_at,
            participant=participant_info(
                identity,
                name=name,
                joined_at=joined_at,
                is_authenticated=is_authenticated,
            ),
        )
    hook(
        "participant_joined",
        start + timedelta(minutes=1),
        participant=participant_info("agent", kind="AGENT"),
    )

    transcript = TranscriptFactory(
        room=room,
        started_by=owner,
        users=[owner],
        started_at=start + timedelta(minutes=2),
    )

    guest_token = generate_token(
        room=str(room.id),
        user=AnonymousUser(),
        username="Гость",
        participant_id="guest-identity",
    )
    for index, (token, text) in enumerate(
        [
            (livekit_token(room, owner), "Всем привет"),
            (livekit_token(room, colleague), "Привет! Документ: https://example.com"),
            (guest_token, "Здравствуйте"),
        ]
    ):
        response = livekit_client(token).post(
            f"/api/v1.0/rooms/{room.id}/chat-messages/",
            {
                "id": f"message-{index}",
                "text": text,
                "sent_at": epoch_milliseconds(start + timedelta(minutes=5 + index)),
                "room_sid": sid,
                "name": None,
            },
            format="json",
        )
        assert response.status_code == 201

    end = start + timedelta(minutes=30)
    transcript.status = TranscriptStatusChoices.READY
    transcript.ended_at = end
    transcript.json_key = f"transcriptions/{transcript.id}/transcript.json"
    transcript.save()
    # The colleague leaves before the end, the others when the call ends
    hook(
        "participant_left",
        start + timedelta(minutes=6),
        participant=participant_info(
            colleague.sub, joined_at=start + timedelta(seconds=20)
        ),
    )
    hook("room_finished", end + timedelta(seconds=20))

    for user, unread in ((owner, 0), (colleague, 1)):
        client = logged_in_client(user)
        calls = client.get("/api/v1.0/calls/").json()
        assert calls["count"] == 1
        call = calls["results"][0]
        assert call["is_active"] is False
        assert call["participants_count"] == 3
        assert call["messages_count"] == 3
        assert call["unread_count"] == unread
        assert call["last_message"]["text"] == "Здравствуйте"
        assert call["has_transcript"] is True

        detail = client.get(f"/api/v1.0/calls/{call['id']}/").json()
        assert [p["name"] for p in detail["participants"]] == [
            "Иван Петров",
            "Мария Смирнова",
            "Гость",
        ]
        assert [p["is_guest"] for p in detail["participants"]] == [False, False, True]
        assert detail["transcripts"][0]["can_view"] is True
        assert detail["can_rejoin"] is False

        messages = client.get(f"/api/v1.0/calls/{call['id']}/messages/").json()
        assert [m["text"] for m in messages["results"]] == [
            "Всем привет",
            "Привет! Документ: https://example.com",
            "Здравствуйте",
        ]

        with mock.patch("calls.views.default_storage") as storage:
            storage.open.return_value = ContentFile(
                json.dumps({"version": 1, "segments": []}).encode(),
                name="transcript.json",
            )
            response = client.get(
                f"/api/v1.0/calls/{call['id']}/transcripts/{transcript.id}/"
            )
        assert response.status_code == 200

    # A guest has no history: a LiveKit token does not open the call list
    response = livekit_client(guest_token).get("/api/v1.0/calls/")
    assert response.status_code == 401
