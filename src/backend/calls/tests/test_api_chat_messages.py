"""Test the endpoint where clients post the chat messages they send."""

# pylint: disable=no-member,unused-argument,too-many-lines

import json
import uuid
from datetime import timedelta
from unittest import mock

from django.conf import settings as django_settings
from django.core.cache import cache
from django.core.files.base import ContentFile
from django.utils import timezone

import pytest
from livekit import api
from rest_framework.test import APIClient

from core.factories import RoomFactory, UserFactory, UserResourceAccessFactory
from core.models import RoleChoices, RoomAccessLevel

from calls import services
from calls.factories import (
    CallParticipantFactory,
    CallSessionFactory,
    ChatMessageFactory,
)
from calls.models import CallParticipant, CallSession, ChatMessage
from calls.tests.utils import (
    epoch_milliseconds,
    livekit_client,
    livekit_token,
    logged_in_client,
    post_webhook,
    webhook_event,
)
from calls.views import ChatMessageRateThrottle, ChatMessageRoomRateThrottle
from transcripts.factories import TranscriptFactory
from transcripts.models import TranscriptStatusChoices

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


def identity_of(token):
    """The LiveKit identity of a token."""
    verifier = api.TokenVerifier(
        django_settings.LIVEKIT_CONFIGURATION["api_key"],
        django_settings.LIVEKIT_CONFIGURATION["api_secret"],
    )
    return verifier.verify(token).identity


def joined_a_minute_ago():
    """A join time, in whole seconds like LiveKit's."""
    return timezone.now().replace(microsecond=0) - timedelta(minutes=1)


def connected_guest(livekit, room, name="Гость"):
    """A guest token of a room, connected to its live room instance."""
    token = livekit_token(room, username=name)
    livekit.connect(
        room, identity_of(token), name=name, joined_at=joined_a_minute_ago()
    )
    return token


def connected_user(livekit, room, user):
    """A user token of a room, connected to its live room instance."""
    livekit.connect(
        room,
        user.sub,
        name=user.full_name,
        is_authenticated=True,
        joined_at=joined_a_minute_ago(),
    )
    return livekit_token(room, user)


def ready_transcript(session):
    """A ready transcript of a call."""
    transcript_id = uuid.uuid4()
    return TranscriptFactory(
        id=transcript_id,
        room=session.room,
        status=TranscriptStatusChoices.READY,
        started_at=session.started_at + timedelta(minutes=1),
        json_key=f"transcriptions/{transcript_id}/transcript.json",
    )


def get_transcript(client, session, transcript):
    """Read a transcript of a call, its document read from a fake storage."""
    with mock.patch("calls.views.default_storage") as storage:
        storage.open.return_value = ContentFile(
            json.dumps({"version": 1, "segments": []}).encode(),
            name="transcript.json",
        )
        return client.get(f"/api/v1.0/calls/{session.id}/transcripts/{transcript.id}/")


def test_chat_messages_disabled(settings):
    """The endpoint does not exist when the call history is disabled."""
    settings.CALLS_HISTORY_ENABLED = False
    room = RoomFactory()

    response = post_message(room, livekit_token(room))

    assert response.status_code == 404
    assert not ChatMessage.objects.exists()


# The sender


def test_chat_messages_authenticated_user(livekit):
    """A user's message is stored in the call of the room sid, as LiveKit
    shows the user: the name of the body is not used.
    """
    user = UserFactory(full_name="Иван Петров")
    room = RoomFactory()
    session = CallSessionFactory(room=room, livekit_sid="RM_first")
    livekit.start_room(room, sid="RM_first", created_at=session.started_at)
    token = connected_user(livekit, room, user)
    joined_at = joined_a_minute_ago()
    sent_at = timezone.now().replace(microsecond=0) - timedelta(seconds=5)

    response = post_message(
        room, token, sent_at=epoch_milliseconds(sent_at), name="Someone else"
    )

    assert response.status_code == 201
    message = ChatMessage.objects.get()
    assert response.json() == {
        "id": CLIENT_ID,
        "call_id": str(session.id),
        "sent_at": sent_at.isoformat().replace("+00:00", "Z"),
    }
    assert message.session == session
    assert message.client_id == CLIENT_ID
    assert message.sender_identity == user.sub
    assert message.sender_user == user
    assert message.sender_name == "Иван Петров"
    assert message.text == "Ссылка на документ: https://example.com/doc"
    assert message.sent_at == sent_at
    session.refresh_from_db()
    assert session.last_activity_at == sent_at
    # Recorded from LiveKit's answer, as its join event would
    participant = CallParticipant.objects.get()
    assert participant.identity == user.sub
    assert participant.user == user
    assert participant.is_guest is False
    assert participant.name == "Иван Петров"
    assert abs(participant.joined_at - joined_at) <= timedelta(seconds=1)


def test_chat_messages_guest(livekit):
    """Guests post their messages too, under the name LiveKit knows."""
    room = RoomFactory()
    CallSessionFactory(room=room, livekit_sid="RM_first")
    livekit.start_room(room)
    token = connected_guest(livekit, room, name="Гость")

    response = post_message(room, token, name="Мария")

    assert response.status_code == 201
    message = ChatMessage.objects.get()
    assert message.sender_user is None
    assert message.sender_name == "Гость"
    participant = CallParticipant.objects.get()
    assert participant.is_guest
    assert participant.user is None
    assert participant.identity == message.sender_identity == identity_of(token)


def test_chat_messages_name_forged_and_backdated(livekit):
    """A participant can neither write under another name nor date a message
    before the real ones (CALLS-2).
    """
    alice = UserFactory(full_name="Alice")
    bob = UserFactory(full_name="Bob")
    room = RoomFactory()
    start = timezone.now().replace(microsecond=0) - timedelta(hours=1)
    session = CallSessionFactory(room=room, livekit_sid="RM_first", started_at=start)
    CallParticipantFactory(session=session, user=alice)
    ChatMessageFactory(
        session=session,
        sender_user=alice,
        sender_name="Alice",
        text="first real message",
        sent_at=start + timedelta(minutes=1),
    )
    livekit.start_room(room, created_at=start)
    before = timezone.now()

    response = post_message(
        room,
        connected_user(livekit, room, bob),
        id="forged",
        text="fabricated opener",
        sent_at=epoch_milliseconds(start - timedelta(hours=5)),
        name="Alice",
    )

    assert response.status_code == 201
    forged = ChatMessage.objects.get(client_id="forged")
    assert forged.sender_name == "Bob"
    assert forged.sender_user == bob
    # Dated at most shortly before it was received
    assert forged.sent_at >= before - timedelta(minutes=2)
    messages = (
        logged_in_client(alice)
        .get(f"/api/v1.0/calls/{session.id}/messages/")
        .json()["results"]
    )
    assert [(m["sender_name"], m["text"]) for m in messages] == [
        ("Alice", "first real message"),
        ("Bob", "fabricated opener"),
    ]


def test_chat_messages_rename_followed(livekit):
    """In-call renames have no webhook: the name LiveKit knows is used, and
    trusted for a short while only.
    """
    room = RoomFactory()
    session = CallSessionFactory(room=room, livekit_sid="RM_first")
    livekit.start_room(room)
    token = connected_guest(livekit, room, name="Гость")
    identity = identity_of(token)

    post_message(room, token, id="first")
    livekit.connect(room, identity, name="Мария", joined_at=joined_a_minute_ago())
    post_message(room, token, id="second")
    # LiveKit's answer is no longer trusted
    cache.delete(
        services._presence_cache_key(session, identity)  # pylint: disable=protected-access
    )
    post_message(room, token, id="third")

    assert CallParticipant.objects.get().name == "Мария"
    assert list(
        ChatMessage.objects.order_by("sent_at", "created_at").values_list(
            "sender_name", flat=True
        )
    ) == ["Гость", "Гость", "Мария"]


def test_chat_messages_presence_cached(livekit):
    """LiveKit is asked once for the messages a participant sends in a row."""
    room = RoomFactory()
    CallSessionFactory(room=room, livekit_sid="RM_first")
    livekit.start_room(room)
    token = connected_guest(livekit, room)

    statuses = [
        post_message(room, token, id=f"message-{index}").status_code
        for index in range(3)
    ]

    assert statuses == [201, 201, 201]
    assert [request[0] for request in livekit.requests] == [
        "list_rooms",
        "get_participant",
    ]
    assert services.PRESENCE_CACHE_SECONDS == 30


def test_chat_messages_existing_participant(livekit):
    """A message goes to the recorded call of its sid and its participant."""
    room = RoomFactory()
    user = UserFactory()
    session = CallSessionFactory(room=room, livekit_sid="RM_first")
    participant = CallParticipantFactory(session=session, user=user, name="Иван")
    livekit.start_room(room)

    response = post_message(room, connected_user(livekit, room, user))

    assert response.status_code == 201
    assert ChatMessage.objects.get().session == session
    assert CallParticipant.objects.get() == participant


def test_chat_messages_sender_not_connected(livekit):
    """A token holder who is not connected to the call cannot post, and does
    not become a participant of the call (CALLS-1).
    """
    owner = UserFactory()
    room = RoomFactory(access_level=RoomAccessLevel.PUBLIC)
    UserResourceAccessFactory(resource=room, user=owner, role=RoleChoices.OWNER)
    session = CallSessionFactory(room=room, livekit_sid="RM_first")
    CallParticipantFactory(session=session, user=owner)
    ChatMessageFactory(session=session, sender_user=owner, text="private plan")
    transcript = ready_transcript(session)
    livekit.start_room(room)
    livekit.connect(room, owner.sub, is_authenticated=True)
    stranger = UserFactory()
    client = logged_in_client(stranger)

    for room_sid in ("RM_first", None):
        response = post_message(
            room, livekit_token(room, stranger), id="x1", room_sid=room_sid
        )
        assert response.status_code == 403

    assert ChatMessage.objects.count() == 1
    assert not CallParticipant.objects.filter(user=stranger).exists()
    assert client.get(f"/api/v1.0/calls/{session.id}/").status_code == 404
    assert client.get(f"/api/v1.0/calls/{session.id}/messages/").status_code == 404
    assert get_transcript(client, session, transcript).status_code == 404


def test_chat_messages_disconnected_participant(livekit):
    """A participant that left the call cannot post in it anymore."""
    room = RoomFactory()
    session = CallSessionFactory(room=room, livekit_sid="RM_first")
    livekit.start_room(room)
    token = connected_guest(livekit, room)
    CallParticipantFactory(session=session, identity=identity_of(token))
    livekit.disconnect(room, identity_of(token))

    response = post_message(room, token)

    assert response.status_code == 403
    assert not ChatMessage.objects.exists()


@pytest.mark.parametrize(
    "kind", [api.ParticipantInfo.AGENT, api.ParticipantInfo.EGRESS]
)
def test_chat_messages_non_human_sender(livekit, kind):
    """Agents and egress are not people of the call."""
    room = RoomFactory()
    CallSessionFactory(room=room, livekit_sid="RM_first")
    livekit.start_room(room)
    token = livekit_token(room)
    livekit.connect(room, identity_of(token), kind=kind)

    response = post_message(room, token)

    assert response.status_code == 403
    assert not CallParticipant.objects.exists()


def test_chat_messages_guest_tokens_farmed(livekit):
    """Fresh guest tokens, fetched without joining, neither post nor add
    participants (CALLS-3).
    """
    room = RoomFactory(access_level=RoomAccessLevel.PUBLIC)
    owner = UserFactory()
    UserResourceAccessFactory(resource=room, user=owner, role=RoleChoices.OWNER)
    session = CallSessionFactory(room=room, livekit_sid="RM_first")
    CallParticipantFactory(session=session, user=owner)
    livekit.start_room(room)
    anonymous = APIClient()

    statuses = set()
    for index in range(5):
        room_data = anonymous.get(f"/api/v1.0/rooms/{room.slug}/").json()
        statuses.add(
            post_message(
                room,
                room_data["livekit"]["token"],
                id=f"spam{index}",
                name=f"User {index}",
            ).status_code
        )

    assert statuses == {403}
    assert CallParticipant.objects.count() == 1
    assert not ChatMessage.objects.exists()


# The call of a message


def test_chat_messages_stale_call_not_reused(livekit):
    """A call whose room_finished was lost is not given the messages of the
    next call, nor opened to the room members who post them (CALLS-1).
    """
    owner = UserFactory()
    member = UserFactory()
    room = RoomFactory(access_level=RoomAccessLevel.RESTRICTED)
    UserResourceAccessFactory(resource=room, user=owner, role=RoleChoices.OWNER)
    UserResourceAccessFactory(resource=room, user=member, role=RoleChoices.MEMBER)
    old_start = timezone.now().replace(microsecond=0) - timedelta(days=3)
    old = CallSessionFactory(room=room, livekit_sid="RM_old", started_at=old_start)
    CallParticipantFactory(
        session=old, user=owner, left_at=old_start + timedelta(hours=1)
    )
    ChatMessageFactory(session=old, sender_user=owner, text="salary numbers")
    transcript = ready_transcript(old)
    client = logged_in_client(member)
    # The real flow: the member fetches a token for the room, without joining
    token = client.get(f"/api/v1.0/rooms/{room.slug}/").json()["livekit"]["token"]
    new_start = timezone.now().replace(microsecond=0) - timedelta(minutes=1)

    # No call is live
    livekit.available = True
    assert post_message(room, token, id="x1", room_sid="RM_new").status_code == 409
    assert post_message(room, token, id="x2", room_sid=None).status_code == 409
    # A new call is live, the member is not in it
    livekit.start_room(room, sid="RM_new", created_at=new_start)
    assert post_message(room, token, id="x3", room_sid="RM_new").status_code == 403
    # The member joins the new call
    livekit.connect(room, member.sub, name="Member", is_authenticated=True)
    response = post_message(room, token, id="x4", room_sid="RM_new")

    assert response.status_code == 201
    new = CallSession.objects.get(livekit_sid="RM_new")
    assert response.json()["call_id"] == str(new.id)
    assert new.started_at == new_start
    assert list(new.messages.values_list("client_id", flat=True)) == ["x4"]
    old.refresh_from_db()
    assert old.ended_at == old_start + timedelta(hours=1)
    assert list(old.messages.values_list("text", flat=True)) == ["salary numbers"]
    assert not old.participants.filter(user=member).exists()
    assert client.get(f"/api/v1.0/calls/{old.id}/").status_code == 404
    assert get_transcript(client, old, transcript).status_code == 404


def test_chat_messages_before_room_started(livekit):
    """A message of a new call posted before its room_started event goes to
    the new call, which the event then finds (CALLS-1).
    """
    owner = UserFactory()
    room = RoomFactory()
    old_start = timezone.now().replace(microsecond=0) - timedelta(days=3)
    old = CallSessionFactory(room=room, livekit_sid="RM_old", started_at=old_start)
    CallParticipantFactory(
        session=old, user=owner, left_at=old_start + timedelta(hours=1)
    )
    new_start = timezone.now().replace(microsecond=0) - timedelta(seconds=30)
    livekit.start_room(room, sid="RM_new", created_at=new_start)

    response = post_message(
        room, connected_user(livekit, room, owner), id="m1", room_sid="RM_new"
    )
    post_webhook(
        webhook_event(
            "room_started",
            room.id,
            sid="RM_new",
            at=new_start,
            room_created_at=new_start,
        )
    )

    assert response.status_code == 201
    new = CallSession.objects.get(livekit_sid="RM_new")
    assert ChatMessage.objects.get().session == new
    assert CallSession.objects.count() == 2
    old.refresh_from_db()
    assert old.ended_at == old_start + timedelta(hours=1)


@pytest.mark.parametrize("room_sid", [None, ""])
def test_chat_messages_without_sid(livekit, room_sid):
    """Without sid, a message goes to the call LiveKit shows live."""
    room = RoomFactory()
    CallSessionFactory(room=room, livekit_sid="RM_old", ended_at=timezone.now())
    current = CallSessionFactory(room=room, livekit_sid="RM_current")
    livekit.start_room(room, sid="RM_current")

    response = post_message(room, connected_guest(livekit, room), room_sid=room_sid)

    assert response.status_code == 201
    assert ChatMessage.objects.get().session == current


def test_chat_messages_call_not_recorded_yet(livekit):
    """A live call whose webhooks did not arrive yet is recorded from LiveKit."""
    room = RoomFactory()
    start = timezone.now().replace(microsecond=0) - timedelta(seconds=20)
    livekit.start_room(room, sid="RM_first", created_at=start)

    response = post_message(room, connected_guest(livekit, room), room_sid=None)

    assert response.status_code == 201
    session = CallSession.objects.get()
    assert session.livekit_sid == "RM_first"
    assert session.is_active
    assert session.started_at == start


@pytest.mark.parametrize("room_sid", [None, "RM_first"])
def test_chat_messages_no_live_call(livekit, room_sid):
    """A message never opens a call that LiveKit does not show (CALLS-3)."""
    room = RoomFactory()
    livekit.available = True

    response = post_message(room, livekit_token(room), room_sid=room_sid)

    assert response.status_code == 409
    assert "detail" in response.json()
    assert not CallSession.objects.exists()
    assert not ChatMessage.objects.exists()


def test_chat_messages_sid_not_live(livekit):
    """A sid that is not the one of the live room instance is refused."""
    room = RoomFactory()
    current = CallSessionFactory(room=room, livekit_sid="RM_current")
    livekit.start_room(room, sid="RM_current")
    token = connected_guest(livekit, room)

    response = post_message(room, token, room_sid="RM_other")

    assert response.status_code == 409
    current.refresh_from_db()
    assert current.ended_at is None
    assert not ChatMessage.objects.exists()


def test_chat_messages_recorded_call_not_live(livekit):
    """The sid of a recorded call still open, while LiveKit shows another
    room instance (or none), is refused: the message is not filed into it.
    """
    room = RoomFactory()
    stale = CallSessionFactory(room=room, livekit_sid="RM_stale")
    livekit.available = True
    token = livekit_token(room)

    no_room = post_message(room, token, id="a", room_sid="RM_stale")
    livekit.start_room(room, sid="RM_current")
    livekit.connect(room, identity_of(token))
    other_room = post_message(room, token, id="b", room_sid="RM_stale")

    assert no_room.status_code == other_room.status_code == 409
    assert not ChatMessage.objects.exists()
    assert not stale.participants.exists()


def test_chat_messages_sid_of_another_room(livekit):
    """The sid of a call of another room is not followed."""
    room = RoomFactory()
    CallSessionFactory(livekit_sid="RM_other")
    livekit.start_room(room, sid="RM_first")
    token = connected_guest(livekit, room)

    response = post_message(room, token, room_sid="RM_other")

    assert response.status_code == 409
    assert not ChatMessage.objects.exists()


def test_chat_messages_livekit_unavailable(livekit):
    """When LiveKit does not answer, the client is told to try again."""
    room = RoomFactory()
    CallSessionFactory(room=room, livekit_sid="RM_first")

    for room_sid in ("RM_first", None):
        response = post_message(room, livekit_token(room), room_sid=room_sid)
        assert response.status_code == 503

    assert not ChatMessage.objects.exists()
    assert not CallParticipant.objects.exists()


# Ended calls


def test_chat_messages_ended_call_retry(livekit):
    """A message retried just after the end of its call is stored, dated
    before the end, without moving the call past its end (CALLS-5).
    """
    room = RoomFactory()
    ended_at = timezone.now().replace(microsecond=0) - timedelta(seconds=30)
    ended = CallSessionFactory(room=room, livekit_sid="RM_first", ended_at=ended_at)
    token = livekit_token(room)
    CallParticipantFactory(
        session=ended, identity=identity_of(token), name="Гость", left_at=ended_at
    )
    CallSessionFactory(room=room, livekit_sid="RM_second")

    response = post_message(room, token, sent_at=epoch_milliseconds(timezone.now()))

    assert response.status_code == 201
    message = ChatMessage.objects.get()
    assert message.session == ended
    assert message.sender_name == "Гость"
    assert message.sent_at == ended_at
    ended.refresh_from_db()
    assert ended.last_activity_at == ended_at
    assert CallParticipant.objects.count() == 1
    # LiveKit is not asked about a call that is over
    assert not livekit.requests


def test_chat_messages_ended_call_not_participant(livekit):
    """An ended call gets no new participant."""
    room = RoomFactory()
    ended = CallSessionFactory(
        room=room, livekit_sid="RM_first", ended_at=timezone.now()
    )

    response = post_message(room, livekit_token(room))

    assert response.status_code == 403
    assert not ChatMessage.objects.exists()
    assert not ended.participants.exists()


def test_chat_messages_ended_call_too_late(livekit):
    """Messages to a call that ended long ago are acknowledged, not stored,
    and the call does not come back up the list (CALLS-5).
    """
    owner = UserFactory()
    room = RoomFactory()
    UserResourceAccessFactory(resource=room, user=owner, role=RoleChoices.OWNER)
    start = timezone.now().replace(microsecond=0) - timedelta(days=7)
    old = CallSessionFactory(
        room=room,
        livekit_sid="RM_old",
        started_at=start,
        ended_at=start + timedelta(hours=1),
    )
    token = livekit_token(room)
    CallParticipantFactory(session=old, identity=identity_of(token))
    CallParticipantFactory(session=old, user=owner)
    other = CallSessionFactory(
        room=room,
        livekit_sid="RM_mid",
        started_at=start + timedelta(days=1),
        ended_at=start + timedelta(days=1, hours=1),
    )
    CallParticipantFactory(session=other, user=owner)

    response = post_message(room, token, id="x3", room_sid="RM_old")

    assert response.status_code == 200
    assert response.json() == {"stored": False}
    assert not ChatMessage.objects.exists()
    old.refresh_from_db()
    assert old.last_activity_at == start
    calls = logged_in_client(owner).get("/api/v1.0/calls/").json()["results"]
    assert [call["id"] for call in calls] == [str(other.id), str(old.id)]


@pytest.mark.parametrize(
    "ended_ago, status", [(timedelta(seconds=30), 403), (timedelta(days=7), 200)]
)
def test_chat_messages_ended_call_later_login(livekit, ended_ago, status):
    """A guest of an ended call who later logs in does not get the call by
    posting to it with its sid (CALLS-5).
    """
    owner = UserFactory()
    room = RoomFactory(access_level=RoomAccessLevel.TRUSTED)
    UserResourceAccessFactory(resource=room, user=owner, role=RoleChoices.OWNER)
    ended_at = timezone.now() - ended_ago
    old = CallSessionFactory(
        room=room,
        livekit_sid="RM_old",
        started_at=ended_at - timedelta(hours=1),
        ended_at=ended_at,
    )
    CallParticipantFactory(session=old, user=owner)
    CallParticipantFactory(session=old, identity="guest-identity")
    ChatMessageFactory(session=old, sender_user=owner, text="secret")
    later = UserFactory()

    response = post_message(
        room, livekit_token(room, later), id="z1", room_sid="RM_old"
    )

    assert response.status_code == status
    assert not ChatMessage.objects.filter(client_id="z1").exists()
    assert not old.participants.filter(user=later).exists()
    assert logged_in_client(later).get(f"/api/v1.0/calls/{old.id}/").status_code == 404


# Message ids


def test_chat_messages_idempotent(livekit):
    """Posting a message again (a retry) does not duplicate it."""
    room = RoomFactory()
    CallSessionFactory(room=room, livekit_sid="RM_first")
    livekit.start_room(room)
    token = connected_guest(livekit, room)

    first = post_message(room, token)
    livekit.available = False
    second = post_message(room, token, text="changed text")

    assert first.status_code == 201
    assert second.status_code == 200
    assert second.json() == first.json()
    message = ChatMessage.objects.get()
    assert message.text == "Ссылка на документ: https://example.com/doc"


def test_chat_messages_same_id_other_identity(livekit):
    """Every participant receives the id of a message: another participant
    posting it first does not keep its sender from storing it (CALLS-2).
    """
    alice = UserFactory(full_name="Alice")
    bob = UserFactory(full_name="Bob")
    room = RoomFactory()
    session = CallSessionFactory(room=room, livekit_sid="RM_first")
    livekit.start_room(room)

    bob_response = post_message(
        room,
        connected_user(livekit, room, bob),
        text="Alice is wrong",
        name="Alice",
    )
    alice_response = post_message(
        room, connected_user(livekit, room, alice), text="The real message"
    )

    assert bob_response.status_code == alice_response.status_code == 201
    assert sorted(
        session.messages.values_list("sender_name", "text", "sender_user")
    ) == [("Alice", "The real message", alice.id), ("Bob", "Alice is wrong", bob.id)]


def test_chat_messages_same_id_other_room(livekit):
    """Message ids are unique per room."""
    room = RoomFactory()
    other_room = RoomFactory()
    CallSessionFactory(room=room, livekit_sid="RM_first")
    CallSessionFactory(room=other_room, livekit_sid="RM_other")
    livekit.start_room(room, sid="RM_first")
    livekit.start_room(other_room, sid="RM_other")
    post_message(other_room, connected_guest(livekit, other_room), room_sid="RM_other")

    response = post_message(room, connected_guest(livekit, room))

    assert response.status_code == 201
    assert ChatMessage.objects.count() == 2


# Validation


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


def test_chat_messages_longest_text(livekit):
    """Messages of 2000 characters, as allowed by the chat, are stored."""
    room = RoomFactory()
    CallSessionFactory(room=room, livekit_sid="RM_first")
    livekit.start_room(room)

    response = post_message(room, connected_guest(livekit, room), text="я" * 2000)

    assert response.status_code == 201
    assert ChatMessage.objects.get().text == "я" * 2000


def test_chat_messages_sent_at_clamped(livekit):
    """Client clocks are only trusted shortly before the message is received,
    and not before the call started (CALLS-2).
    """
    room = RoomFactory()
    CallSessionFactory(room=room, livekit_sid="RM_first")
    livekit.start_room(room)
    token = connected_guest(livekit, room)
    recent_room = RoomFactory()
    recent_start = timezone.now().replace(microsecond=0) - timedelta(seconds=30)
    recent = CallSessionFactory(
        room=recent_room, livekit_sid="RM_recent", started_at=recent_start
    )
    livekit.start_room(recent_room, sid="RM_recent", created_at=recent_start)
    recent_token = connected_guest(livekit, recent_room)
    before = timezone.now()

    future = post_message(
        room, token, id="future", sent_at=epoch_milliseconds(before + timedelta(days=1))
    )
    past = post_message(
        room, token, id="past", sent_at=epoch_milliseconds(before - timedelta(days=1))
    )
    before_start = post_message(
        recent_room,
        recent_token,
        id="before-start",
        room_sid="RM_recent",
        sent_at=epoch_milliseconds(before - timedelta(days=1)),
    )
    after = timezone.now()

    assert future.status_code == past.status_code == before_start.status_code == 201
    future_message = ChatMessage.objects.get(client_id="future")
    assert before <= future_message.sent_at <= after
    past_message = ChatMessage.objects.get(client_id="past")
    assert (
        before - timedelta(minutes=2)
        <= past_message.sent_at
        <= after - timedelta(minutes=2)
    )
    assert ChatMessage.objects.get(
        client_id="before-start"
    ).sent_at == recent.started_at - timedelta(minutes=1)


# Access


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


# Throttling


def test_chat_messages_throttled(livekit):
    """A participant cannot post more than the rate allows."""
    assert ChatMessageRateThrottle.rate == "120/minute"
    room = RoomFactory()
    CallSessionFactory(room=room, livekit_sid="RM_first")
    livekit.start_room(room)
    token = connected_guest(livekit, room)
    other_token = connected_guest(livekit, room)

    with mock.patch.object(ChatMessageRateThrottle, "rate", "2/minute"):
        statuses = [
            post_message(room, token, id=f"message-{index}").status_code
            for index in range(3)
        ]
        other_status = post_message(room, other_token, id="other").status_code

    assert statuses == [201, 201, 429]
    # Throttled per participant
    assert other_status == 201


def test_chat_messages_throttled_per_room(livekit):
    """The messages posted in a room are limited, whoever posts them: fresh
    guest tokens do not bypass the limit (CALLS-3).
    """
    assert ChatMessageRoomRateThrottle.rate == "600/minute"
    room = RoomFactory()
    other_room = RoomFactory()
    CallSessionFactory(room=room, livekit_sid="RM_first")
    CallSessionFactory(room=other_room, livekit_sid="RM_other")
    livekit.start_room(room, sid="RM_first")
    livekit.start_room(other_room, sid="RM_other")

    with mock.patch.object(ChatMessageRoomRateThrottle, "rate", "2/minute"):
        statuses = [
            post_message(room, connected_guest(livekit, room)).status_code
            for _index in range(3)
        ]
        other_status = post_message(
            other_room, connected_guest(livekit, other_room), room_sid="RM_other"
        ).status_code

    assert statuses == [201, 201, 429]
    # Throttled per room
    assert other_status == 201
