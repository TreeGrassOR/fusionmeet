"""Test the call services: concurrency fallbacks and participant updates."""

# pylint: disable=unused-argument

import json
from datetime import timedelta
from unittest import mock

from django.db import IntegrityError
from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from core.factories import RoomFactory, UserFactory

from calls import services
from calls.factories import (
    CallParticipantFactory,
    CallSessionFactory,
    ChatMessageFactory,
)
from calls.models import CallParticipant, CallSession, ChatMessage
from calls.tests.utils import (
    CALL_START,
    WEBHOOK_URL,
    epoch_milliseconds,
    epoch_seconds,
    participant_info,
    post_webhook,
    sign_webhook,
    webhook_event,
)

pytestmark = pytest.mark.django_db


def test_from_timestamps():
    """LiveKit timestamps are converted exactly; 0 means unknown."""
    assert services.from_milliseconds(0) is None
    assert services.from_seconds(0) is None
    assert services.from_seconds(epoch_seconds(CALL_START)) == CALL_START
    assert services.from_milliseconds(
        epoch_milliseconds(CALL_START) + 123
    ) == CALL_START + timedelta(milliseconds=123)


def test_ensure_session_retries_after_concurrent_creation():
    """A session created concurrently is found on the next attempt."""
    room = RoomFactory()
    real = services._get_or_create_session  # pylint: disable=protected-access

    calls = []

    def concurrent(*args):
        calls.append(args)
        if len(calls) == 1:
            raise IntegrityError("duplicate key")
        return real(*args)

    with mock.patch("calls.services._get_or_create_session", side_effect=concurrent):
        session = services.ensure_session(room, "RM_first", CALL_START)

    assert len(calls) == 2
    assert session.livekit_sid == "RM_first"


def test_ensure_session_gives_up():
    """Repeated integrity errors are raised, so that LiveKit retries the event."""
    room = RoomFactory()

    with (
        mock.patch(
            "calls.services._get_or_create_session",
            side_effect=IntegrityError("duplicate key"),
        ) as get_or_create,
        pytest.raises(IntegrityError),
    ):
        services.ensure_session(room, "RM_first", CALL_START)

    assert get_or_create.call_count == services.SESSION_CREATION_ATTEMPTS


def test_webhook_database_error_is_retried():
    """A database error gives a 500: LiveKit sends the event again."""
    room = RoomFactory()
    body = json.dumps(webhook_event("room_started", room.id))

    with mock.patch(
        "calls.services._get_or_create_session",
        side_effect=IntegrityError("duplicate key"),
    ):
        response = APIClient(raise_request_exception=False).post(
            WEBHOOK_URL,
            data=body,
            content_type="application/webhook+json",
            HTTP_AUTHORIZATION=sign_webhook(body),
        )

    assert response.status_code == 500


def test_record_join_concurrent_creation():
    """A participant created concurrently is updated instead."""
    session = CallSessionFactory()
    existing = CallParticipantFactory(session=session, identity="guest-1", name="Old")
    real_select_for_update = CallParticipant.objects.select_for_update
    lookups = []

    def select_for_update():
        lookups.append(True)
        if len(lookups) == 1:
            # The first lookup misses the row, as if it was inserted meanwhile
            return CallParticipant.objects.none()
        return real_select_for_update()

    with mock.patch.object(
        CallParticipant.objects, "select_for_update", side_effect=select_for_update
    ):
        services.record_join(
            session,
            identity="guest-1",
            name="New",
            user=None,
            joined_at=existing.joined_at,
        )

    assert len(lookups) == 2
    existing.refresh_from_db()
    assert existing.name == "New"
    assert CallParticipant.objects.count() == 1


def test_record_join_completes_participant_known_from_chat():
    """The join event of a participant first seen in the chat completes it."""
    user = UserFactory()
    session = CallSessionFactory()
    message_time = session.started_at + timedelta(minutes=5)
    participant = CallParticipantFactory(
        session=session,
        identity=user.sub,
        user=None,
        name="Ivan",
        joined_at=message_time,
        last_joined_at=session.started_at,
    )
    joined_at = session.started_at + timedelta(minutes=1)

    services.record_join(
        session, identity=user.sub, name="Иван", user=user, joined_at=joined_at
    )

    participant.refresh_from_db()
    assert participant.user == user
    assert participant.is_guest is False
    assert participant.name == "Иван"
    assert participant.joined_at == joined_at
    assert participant.last_joined_at == joined_at


def test_record_sender_concurrent_join():
    """A sender recorded concurrently by its join event is not duplicated."""
    session = CallSessionFactory()

    with mock.patch.object(
        CallParticipant.objects, "create", side_effect=IntegrityError("duplicate")
    ):
        services.record_sender(
            session, identity="guest-1", user=None, name="Гость", at=timezone.now()
        )

    assert not CallParticipant.objects.exists()


@pytest.mark.parametrize("identity, conflict", [("guest-1", False), ("guest-2", True)])
def test_store_chat_message_concurrent_post(identity, conflict):
    """A message posted twice at the same time is stored once."""
    room = RoomFactory()
    session = CallSessionFactory(room=room)
    stored = ChatMessageFactory(
        session=session, client_id="same-id", sender_identity="guest-1"
    )
    real_filter = ChatMessage.objects.filter

    def filter_messages(*args, **kwargs):
        if "session__room" in kwargs:
            # The first lookup misses the message, as if it was stored meanwhile
            return ChatMessage.objects.none()
        return real_filter(*args, **kwargs)

    def store():
        return services.store_chat_message(
            room,
            identity=identity,
            user=None,
            name="Гость",
            client_id="same-id",
            text="Привет",
            sent_at=timezone.now(),
            room_sid=session.livekit_sid,
        )

    with mock.patch.object(ChatMessage.objects, "filter", side_effect=filter_messages):
        if conflict:
            with pytest.raises(services.ChatMessageConflictError):
                store()
        else:
            assert store() == (stored, False)

    assert ChatMessage.objects.count() == 1


def test_store_chat_message_unexpected_integrity_error():
    """An integrity error that is not a duplicate is raised."""
    room = RoomFactory()

    with (
        mock.patch.object(
            ChatMessage.objects, "create", side_effect=IntegrityError("other")
        ),
        pytest.raises(IntegrityError),
    ):
        services.store_chat_message(
            room,
            identity="guest-1",
            user=None,
            name="Гость",
            client_id="an-id",
            text="Привет",
            sent_at=timezone.now(),
            room_sid=None,
        )


def test_webhook_invalid_filter_regex(settings):
    """An invalid LIVEKIT_WEBHOOK_EVENTS_FILTER_REGEX disables the filter."""
    settings.LIVEKIT_WEBHOOK_EVENTS_FILTER_REGEX = "["
    room = RoomFactory()

    response = post_webhook(webhook_event("room_started", room.id))

    assert response.status_code == 200
    assert CallSession.objects.exists()


def test_webhook_participant_without_identity():
    """A participant without identity is ignored."""
    room = RoomFactory()

    response = post_webhook(
        webhook_event("participant_joined", room.id, participant=participant_info(""))
    )

    assert response.status_code == 200
    assert not CallParticipant.objects.exists()


def test_webhook_event_without_time():
    """An event without creation time is dated when it is received."""
    room = RoomFactory()
    session = CallSessionFactory(room=room, livekit_sid="RM_first")
    CallParticipantFactory(session=session)
    event = webhook_event("room_finished", room.id)
    del event["createdAt"]
    before = timezone.now()

    post_webhook(event)

    session.refresh_from_db()
    assert before <= session.ended_at <= timezone.now()


def test_webhook_non_utf8_body():
    """A body that is not UTF-8 cannot match a signature."""
    response = APIClient().post(
        WEBHOOK_URL,
        data=b"\xff\xfe\x00",
        content_type="application/webhook+json",
        HTTP_AUTHORIZATION="token",
    )

    assert response.status_code == 401


def test_models_str():
    """Models have readable representations."""
    participant = CallParticipantFactory(name="Иван")
    message = ChatMessageFactory(session=participant.session, client_id="abc")

    assert str(participant.session) == (
        f"Call {participant.session.id} in room {participant.session.room_id}"
    )
    assert str(participant) == f"Иван in call {participant.session_id}"
    assert str(message) == f"Message abc in call {message.session_id}"
