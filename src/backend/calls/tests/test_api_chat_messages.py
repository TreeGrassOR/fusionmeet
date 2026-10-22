"""Test the endpoint where clients post the chat messages they send."""

# pylint: disable=unused-argument

from datetime import timedelta
from unittest import mock

from django.utils import timezone

import pytest

from core.factories import RoomFactory, UserFactory

from calls.factories import CallParticipantFactory, CallSessionFactory
from calls.models import CallParticipant, CallSession, ChatMessage
from calls.tests.utils import (
    epoch_milliseconds,
    livekit_client,
    livekit_token,
    logged_in_client,
)
from calls.views import ChatMessageRateThrottle

pytestmark = pytest.mark.django_db

CLIENT_ID = "3f0a7d0e-5b8e-4f6e-9c1d-2a4b6c8d0e1f"


def message_body(**overrides):
    """Body of a posted chat message."""
    return {
        "id": CLIENT_ID,
        "text": "Ссылка на документ: https://example.com/doc",
        "sent_at": epoch_milliseconds(timezone.now()),
        "room_sid": "RM_first",
        "name": "Мария",
        **overrides,
    }


def post_message(room, token, **overrides):
    """Post a chat message to a room with a LiveKit token."""
    return livekit_client(token).post(
        f"/api/v1.0/rooms/{room.id}/chat-messages/",
        message_body(**overrides),
        format="json",
    )


def test_chat_messages_disabled(settings):
    """The endpoint does not exist when the call history is disabled."""
    settings.CALLS_HISTORY_ENABLED = False
    room = RoomFactory()

    response = post_message(room, livekit_token(room))

    assert response.status_code == 404
    assert not ChatMessage.objects.exists()


def test_chat_messages_authenticated_user(settings):
    """A user's message is stored in the call of the room sid, as the user."""
    settings.AUTHENTICATED_PARTICIPANTS_CAN_EDIT_DISPLAY_NAME = False
    user = UserFactory(full_name="Иван Петров")
    room = RoomFactory()
    sent_at = timezone.now().replace(microsecond=0) - timedelta(seconds=5)

    response = post_message(
        room,
        livekit_token(room, user),
        sent_at=epoch_milliseconds(sent_at),
        name="Someone else",
    )

    assert response.status_code == 201
    message = ChatMessage.objects.get()
    session = CallSession.objects.get()
    assert response.json() == {
        "id": CLIENT_ID,
        "call_id": str(session.id),
        "sent_at": sent_at.isoformat().replace("+00:00", "Z"),
    }
    assert session.room == room
    # The sid comes from the webhooks only
    assert session.livekit_sid is None
    assert session.started_at == session.last_activity_at == sent_at
    assert message.session == session
    assert message.client_id == CLIENT_ID
    assert message.sender_identity == user.sub
    assert message.sender_user == user
    # The display name cannot be chosen: the name of the token is used
    assert message.sender_name == "Иван Петров"
    assert message.text == "Ссылка на документ: https://example.com/doc"
    assert message.sent_at == sent_at
    participant = CallParticipant.objects.get()
    assert participant.identity == user.sub
    assert participant.user == user
    assert participant.is_guest is False
    assert participant.joined_at == sent_at


def test_chat_messages_guest():
    """Guests post their messages too, under the name they chose."""
    room = RoomFactory()

    response = post_message(room, livekit_token(room, username="Гость"), name="Мария")

    assert response.status_code == 201
    message = ChatMessage.objects.get()
    assert message.sender_user is None
    assert message.sender_name == "Мария"
    participant = CallParticipant.objects.get()
    assert participant.is_guest
    assert participant.user is None
    assert participant.identity == message.sender_identity


def test_chat_messages_name_falls_back_to_token():
    """Without a name, the name of the token is used."""
    room = RoomFactory()

    response = post_message(room, livekit_token(room, username="Гость"), name=None)

    assert response.status_code == 201
    assert ChatMessage.objects.get().sender_name == "Гость"


def test_chat_messages_rename_updates_participant():
    """In-call renames have no webhook: the name of the latest message is kept."""
    room = RoomFactory()
    token = livekit_token(room, username="Гость")

    post_message(room, token, name="Гость")
    post_message(room, token, id="second-message", name="Мария")

    assert CallParticipant.objects.get().name == "Мария"
    assert list(
        ChatMessage.objects.order_by("sent_at", "created_at").values_list(
            "sender_name", flat=True
        )
    ) == ["Гость", "Мария"]


def test_chat_messages_existing_call_and_participant():
    """A message goes to the recorded call of its sid and its participant."""
    room = RoomFactory()
    user = UserFactory()
    session = CallSessionFactory(room=room, livekit_sid="RM_first")
    participant = CallParticipantFactory(session=session, user=user, name="Иван")

    response = post_message(room, livekit_token(room, user))

    assert response.status_code == 201
    assert ChatMessage.objects.get().session == session
    assert CallParticipant.objects.get() == participant


def test_chat_messages_ended_call():
    """A message posted late (retried) goes to the call of its sid, even ended."""
    room = RoomFactory()
    ended = CallSessionFactory(
        room=room, livekit_sid="RM_first", ended_at=timezone.now()
    )
    CallSessionFactory(room=room, livekit_sid="RM_second")

    response = post_message(room, livekit_token(room))

    assert response.status_code == 201
    assert ChatMessage.objects.get().session == ended


@pytest.mark.parametrize("room_sid", [None, ""])
def test_chat_messages_without_sid_open_call(room_sid):
    """Without sid, a message goes to the open call of the room."""
    room = RoomFactory()
    CallSessionFactory(room=room, livekit_sid="RM_old", ended_at=timezone.now())
    current = CallSessionFactory(room=room, livekit_sid="RM_current")

    response = post_message(room, livekit_token(room), room_sid=room_sid)

    assert response.status_code == 201
    assert ChatMessage.objects.get().session == current


def test_chat_messages_without_sid_no_call():
    """Without a recorded call, a call is opened, to be completed by webhooks."""
    room = RoomFactory()
    sent_at = timezone.now().replace(microsecond=0) - timedelta(seconds=5)

    response = post_message(
        room, livekit_token(room), room_sid=None, sent_at=epoch_milliseconds(sent_at)
    )

    assert response.status_code == 201
    session = CallSession.objects.get()
    assert session.livekit_sid is None
    assert session.is_active
    assert session.started_at == session.last_activity_at == sent_at


def test_chat_messages_unknown_sid_open_call():
    """A client never closes a call: a message with an unknown sid goes to the
    open call of the room, which only webhooks close.
    """
    room = RoomFactory()
    current = CallSessionFactory(room=room, livekit_sid="RM_current")
    CallParticipantFactory(session=current)

    response = post_message(room, livekit_token(room), room_sid="RM_new")

    assert response.status_code == 201
    current.refresh_from_db()
    assert current.ended_at is None
    assert current.livekit_sid == "RM_current"
    assert ChatMessage.objects.get().session == current


def test_chat_messages_sid_of_another_room():
    """The sid of a call of another room is not followed."""
    room = RoomFactory()
    other_call = CallSessionFactory(livekit_sid="RM_other")

    response = post_message(room, livekit_token(room), room_sid="RM_other")

    assert response.status_code == 201
    message = ChatMessage.objects.get()
    assert message.session != other_call
    assert message.session.room == room
    assert message.session.livekit_sid is None


def test_chat_messages_idempotent():
    """Posting a message again (a retry) does not duplicate it."""
    room = RoomFactory()
    token = livekit_token(room)

    first = post_message(room, token)
    second = post_message(room, token, text="changed text")

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json() == first.json()
    message = ChatMessage.objects.get()
    assert message.text == "Ссылка на документ: https://example.com/doc"


def test_chat_messages_same_id_other_identity():
    """A message id already posted by another participant is a conflict."""
    room = RoomFactory()
    post_message(room, livekit_token(room))

    response = post_message(room, livekit_token(room))

    assert response.status_code == 409
    assert ChatMessage.objects.count() == 1


def test_chat_messages_same_id_other_room():
    """Message ids are unique per room."""
    room = RoomFactory()
    other_room = RoomFactory()
    post_message(other_room, livekit_token(other_room))

    response = post_message(room, livekit_token(room))

    assert response.status_code == 201
    assert ChatMessage.objects.count() == 2


@pytest.mark.parametrize(
    "overrides, field",
    [
        ({"text": "x" * 2001}, "text"),
        ({"text": ""}, "text"),
        ({"text": "   \n "}, "text"),
        ({"id": ""}, "id"),
        ({"id": "x" * 65}, "id"),
        ({"id": "../../etc"}, "id"),
        ({"sent_at": "yesterday"}, "sent_at"),
        ({"sent_at": -1}, "sent_at"),
        ({"sent_at": 10**15}, "sent_at"),
        ({"room_sid": "PA_participant"}, "room_sid"),
        ({"name": "x" * 256}, "name"),
    ],
)
def test_chat_messages_invalid(overrides, field):
    """Invalid messages are refused."""
    room = RoomFactory()

    response = post_message(room, livekit_token(room), **overrides)

    assert response.status_code == 400
    assert field in response.json()
    assert not ChatMessage.objects.exists()


def test_chat_messages_longest_text():
    """Messages of 2000 characters, as allowed by the chat, are stored."""
    room = RoomFactory()

    response = post_message(room, livekit_token(room), text="я" * 2000)

    assert response.status_code == 201
    assert ChatMessage.objects.get().text == "я" * 2000


def test_chat_messages_sent_at_clamped():
    """Client clocks are not trusted beyond the call start and now."""
    room = RoomFactory()
    session = CallSessionFactory(room=room, livekit_sid="RM_first")
    token = livekit_token(room)
    before = timezone.now()

    future = post_message(
        room, token, id="future", sent_at=epoch_milliseconds(before + timedelta(days=1))
    )
    past = post_message(
        room, token, id="past", sent_at=epoch_milliseconds(before - timedelta(days=1))
    )

    assert future.status_code == past.status_code == 201
    future_message = ChatMessage.objects.get(client_id="future")
    assert before <= future_message.sent_at <= timezone.now()
    past_message = ChatMessage.objects.get(client_id="past")
    assert past_message.sent_at == session.started_at - timedelta(minutes=1)


def test_chat_messages_token_for_another_room():
    """A token for another room is refused."""
    room = RoomFactory()
    other_room = RoomFactory()

    response = post_message(room, livekit_token(other_room))

    assert response.status_code == 403
    assert not ChatMessage.objects.exists()


def test_chat_messages_without_token():
    """A logged-in user without LiveKit token is refused."""
    room = RoomFactory()

    response = logged_in_client(UserFactory()).post(
        f"/api/v1.0/rooms/{room.id}/chat-messages/", message_body(), format="json"
    )

    assert response.status_code in (401, 403)
    assert not ChatMessage.objects.exists()


def test_chat_messages_invalid_token():
    """A token not signed by LiveKit is refused."""
    room = RoomFactory()

    response = post_message(room, "not-a-token")

    assert response.status_code in (401, 403)
    assert not ChatMessage.objects.exists()


def test_chat_messages_unknown_room():
    """Messages of an unknown room are refused."""
    room = RoomFactory()
    token = livekit_token(room)
    room_id = room.id
    room.delete()

    response = livekit_client(token).post(
        f"/api/v1.0/rooms/{room_id}/chat-messages/", message_body(), format="json"
    )

    assert response.status_code == 404


def test_chat_messages_deleted_room():
    """Messages of a deleted room are refused."""
    room = RoomFactory()
    token = livekit_token(room)
    room.soft_delete()

    response = post_message(room, token)

    assert response.status_code == 410
    assert not ChatMessage.objects.exists()


def test_chat_messages_throttled():
    """A participant cannot post more than the rate allows."""
    assert ChatMessageRateThrottle.rate == "120/minute"
    room = RoomFactory()
    token = livekit_token(room)
    other_token = livekit_token(room)

    with mock.patch.object(ChatMessageRateThrottle, "rate", "2/minute"):
        statuses = [
            post_message(room, token, id=f"message-{index}").status_code
            for index in range(3)
        ]
        other_status = post_message(room, other_token, id="other").status_code

    assert statuses == [201, 201, 429]
    # Throttled per participant
    assert other_status == 201
