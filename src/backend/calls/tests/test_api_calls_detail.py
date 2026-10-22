"""Test the detail of a call, its messages and its read marker."""

# pylint: disable=unused-argument

from datetime import timedelta

from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from core.factories import RecordingFactory, RoomFactory, UserFactory
from core.models import RecordingModeChoices, RecordingStatusChoices, RoleChoices

from calls.factories import (
    CallParticipantFactory,
    CallSessionFactory,
    ChatMessageFactory,
)
from calls.models import CallUserState
from calls.tests.utils import logged_in_client
from transcripts.factories import TranscriptFactory
from transcripts.models import TranscriptStatusChoices

pytestmark = pytest.mark.django_db


def iso(moment):
    """A datetime as the API writes it."""
    return moment.isoformat().replace("+00:00", "Z")


def call_url(session, suffix=""):
    """URL of a call endpoint."""
    return f"/api/v1.0/calls/{session.id}/{suffix}"


# Detail


def test_call_detail_anonymous():
    """Anonymous users cannot see calls."""
    session = CallParticipantFactory().session

    assert APIClient().get(call_url(session)).status_code == 401


def test_call_detail_disabled(settings):
    """The endpoint does not exist when the call history is disabled."""
    settings.CALLS_HISTORY_ENABLED = False
    user = UserFactory()
    session = CallParticipantFactory(user=user).session

    assert logged_in_client(user).get(call_url(session)).status_code == 404


def test_call_detail_not_visible():
    """Users cannot see calls they did not attend."""
    user = UserFactory()
    room = RoomFactory(users=[(user, RoleChoices.MEMBER)])
    session = CallParticipantFactory(session__room=room).session

    assert logged_in_client(user).get(call_url(session)).status_code == 404


def test_call_detail_unknown():
    """Unknown calls are not found."""
    user = UserFactory()

    response = logged_in_client(user).get(
        "/api/v1.0/calls/7c1c0d3e-1111-4a5b-9c8d-0e0f00000000/"
    )

    assert response.status_code == 404


def test_call_detail():
    """The detail lists every participant, the transcripts and the recordings."""
    user = UserFactory()
    room = RoomFactory(name="abc-defg-hij")
    start = timezone.now().replace(microsecond=0) - timedelta(hours=1)
    session = CallSessionFactory(room=room, started_at=start)
    me = CallParticipantFactory(
        session=session,
        user=user,
        name="Иван",
        left_at=start + timedelta(minutes=10),
    )
    guest = CallParticipantFactory(
        session=session, name="Гость", joined_at=start + timedelta(minutes=2)
    )
    for index in range(6):
        CallParticipantFactory(
            session=session,
            name=f"Person {index}",
            joined_at=start + timedelta(minutes=3 + index),
        )
    own_transcript = TranscriptFactory(
        room=room,
        users=[user],
        status=TranscriptStatusChoices.READY,
        started_at=start + timedelta(minutes=1),
        ended_at=start + timedelta(minutes=9),
    )
    other_transcript = TranscriptFactory(
        room=room,
        status=TranscriptStatusChoices.ACTIVE,
        started_at=start + timedelta(minutes=20),
    )
    recording = RecordingFactory(
        room=room,
        users=[user],
        status=RecordingStatusChoices.SAVED,
        mode=RecordingModeChoices.SCREEN_RECORDING,
    )
    RecordingFactory(room=room, status=RecordingStatusChoices.SAVED)

    response = logged_in_client(user).get(call_url(session))

    assert response.status_code == 200
    data = response.json()
    assert data["id"] == str(session.id)
    assert data["room"] == {"id": str(room.id), "slug": "abc-defg-hij"}
    assert data["participants_count"] == 8
    assert data["participants"][:2] == [
        {
            "name": "Иван",
            "is_me": True,
            "is_guest": False,
            "joined_at": iso(me.joined_at),
            "left_at": iso(me.left_at),
        },
        {
            "name": "Гость",
            "is_me": False,
            "is_guest": True,
            "joined_at": iso(guest.joined_at),
            "left_at": None,
        },
    ]
    assert len(data["participants"]) == 8
    assert data["transcripts"] == [
        {
            "id": str(own_transcript.id),
            "status": "ready",
            "started_at": iso(own_transcript.started_at),
            "ended_at": iso(own_transcript.ended_at),
            "can_view": True,
        },
        {
            "id": str(other_transcript.id),
            "status": "active",
            "started_at": iso(other_transcript.started_at),
            "ended_at": None,
            "can_view": True,
        },
    ]
    assert data["has_transcript"] is True
    assert data["recordings"] == [
        {
            "id": str(recording.id),
            "status": "saved",
            "mode": "screen_recording",
            "created_at": iso(recording.created_at),
        }
    ]
    assert data["has_recording"] is True
    assert data["can_rejoin"] is True


def test_call_detail_transcripts_not_viewable(settings):
    """Without the participants setting, only transcript users can read them."""
    settings.CALLS_TRANSCRIPTS_VISIBLE_TO_PARTICIPANTS = False
    user = UserFactory()
    session = CallParticipantFactory(user=user).session
    TranscriptFactory(
        room=session.room, started_at=session.started_at + timedelta(minutes=1)
    )

    data = logged_in_client(user).get(call_url(session)).json()

    assert [t["can_view"] for t in data["transcripts"]] == [False]
    assert data["has_transcript"] is False


def test_call_detail_room_owner_absent():
    """A room owner who did not attend reads the transcripts given to them."""
    user = UserFactory()
    room = RoomFactory(users=[(user, RoleChoices.OWNER)])
    session = CallParticipantFactory(session__room=room).session
    start = session.started_at
    TranscriptFactory(
        room=room,
        started_at=start + timedelta(minutes=1),
        users=[user],
        status=TranscriptStatusChoices.READY,
    )
    TranscriptFactory(room=room, started_at=start + timedelta(minutes=2))

    data = logged_in_client(user).get(call_url(session)).json()

    assert [t["can_view"] for t in data["transcripts"]] == [True, False]
    assert data["participants"][0]["is_me"] is False


@pytest.mark.parametrize(
    "ended, deleted, expected",
    [(False, False, True), (True, False, False), (False, True, False)],
)
def test_call_detail_can_rejoin(ended, deleted, expected):
    """A call can be rejoined while it goes on, unless its room was deleted."""
    user = UserFactory()
    session = CallSessionFactory(ended_at=timezone.now() if ended else None)
    CallParticipantFactory(session=session, user=user)
    if deleted:
        session.room.soft_delete()

    data = logged_in_client(user).get(call_url(session)).json()

    assert data["can_rejoin"] is expected


def test_call_detail_closes_call_over(livekit):
    """A call whose room_finished was lost is shown ended once LiveKit shows
    its room is gone (CALLS-4).
    """
    user = UserFactory()
    start = timezone.now().replace(microsecond=0) - timedelta(days=2)
    session = CallSessionFactory(started_at=start)
    CallParticipantFactory(
        session=session, user=user, left_at=start + timedelta(hours=1)
    )
    livekit.available = True

    data = logged_in_client(user).get(call_url(session)).json()

    assert data["is_active"] is False
    assert data["can_rejoin"] is False
    assert data["ended_at"] == iso(start + timedelta(hours=1))


def test_call_detail_live_call_kept_open(livekit):
    """A call LiveKit shows live stays open."""
    user = UserFactory()
    session = CallSessionFactory()
    CallParticipantFactory(session=session, user=user)
    livekit.start_room(session.room, sid=session.livekit_sid)

    data = logged_in_client(user).get(call_url(session)).json()

    assert data["is_active"] is True
    assert data["can_rejoin"] is True
    assert livekit.requests == [("list_rooms", [str(session.room_id)])]


def test_call_detail_consecutive_calls():
    """A transcript or recording started at the end of a call belongs to that
    call only, not to the next call of the room (CALLS-6).
    """
    user = UserFactory()
    newcomer = UserFactory()
    room = RoomFactory()
    first_start = timezone.now().replace(microsecond=0) - timedelta(hours=2)
    first = CallSessionFactory(
        room=room,
        started_at=first_start,
        ended_at=first_start + timedelta(minutes=30),
    )
    CallParticipantFactory(session=first, user=user)
    second = CallSessionFactory(
        room=room, started_at=first.ended_at + timedelta(seconds=20)
    )
    CallParticipantFactory(session=second, user=user)
    CallParticipantFactory(session=second, user=newcomer)
    near_end = TranscriptFactory(
        room=room,
        status=TranscriptStatusChoices.READY,
        started_at=first.ended_at - timedelta(seconds=5),
    )
    at_end = TranscriptFactory(
        room=room, status=TranscriptStatusChoices.READY, started_at=first.ended_at
    )
    between = TranscriptFactory(
        room=room,
        status=TranscriptStatusChoices.READY,
        started_at=first.ended_at + timedelta(seconds=10),
    )
    recordings = {}
    for key, created_at in (
        ("near_end", first.ended_at - timedelta(seconds=5)),
        ("at_end", first.ended_at),
        ("between", first.ended_at + timedelta(seconds=10)),
    ):
        recordings[key] = RecordingFactory(
            room=room, users=[user], status=RecordingStatusChoices.SAVED
        )
        type(recordings[key]).objects.filter(pk=recordings[key].pk).update(
            created_at=created_at
        )
    client = logged_in_client(user)

    first_data = client.get(call_url(first)).json()
    second_data = client.get(call_url(second)).json()

    assert [t["id"] for t in first_data["transcripts"]] == [
        str(near_end.id),
        str(at_end.id),
    ]
    assert [t["id"] for t in second_data["transcripts"]] == [str(between.id)]
    assert [r["id"] for r in first_data["recordings"]] == [
        str(recordings["near_end"].id),
        str(recordings["at_end"].id),
    ]
    assert [r["id"] for r in second_data["recordings"]] == [
        str(recordings["between"].id)
    ]
    # Not readable through the next call by someone who was not in the first
    response = logged_in_client(newcomer).get(
        call_url(second, f"transcripts/{near_end.id}/")
    )
    assert response.status_code == 404


# Messages


def test_call_messages():
    """The messages of a call are listed oldest first."""
    user = UserFactory()
    session = CallSessionFactory()
    start = session.started_at
    CallParticipantFactory(session=session, user=user)
    second = ChatMessageFactory(
        session=session,
        sender_user=user,
        sender_name="Иван",
        text="Второе",
        sent_at=start + timedelta(minutes=2),
    )
    first = ChatMessageFactory(
        session=session,
        sender_name="Мария",
        text="Первое",
        sent_at=start + timedelta(minutes=1),
    )
    ChatMessageFactory()  # another call

    response = logged_in_client(user).get(call_url(session, "messages/"))

    assert response.status_code == 200
    assert response.json() == {
        "count": 2,
        "next": None,
        "previous": None,
        "results": [
            {
                "id": str(first.id),
                "sender_name": "Мария",
                "sender_is_me": False,
                "text": "Первое",
                "sent_at": iso(first.sent_at),
            },
            {
                "id": str(second.id),
                "sender_name": "Иван",
                "sender_is_me": True,
                "text": "Второе",
                "sent_at": iso(second.sent_at),
            },
        ],
    }


def test_call_messages_pagination():
    """Messages are paginated."""
    user = UserFactory()
    session = CallSessionFactory()
    CallParticipantFactory(session=session, user=user)
    for minute in range(5):
        ChatMessageFactory(
            session=session, sent_at=session.started_at + timedelta(minutes=minute)
        )

    client = logged_in_client(user)
    first_page = client.get(call_url(session, "messages/?page_size=3")).json()
    second_page = client.get(call_url(session, "messages/?page_size=3&page=2")).json()

    assert first_page["count"] == 5
    assert len(first_page["results"]) == 3
    assert len(second_page["results"]) == 2


def test_call_messages_not_visible():
    """Messages of a call the user cannot see are not found."""
    user = UserFactory()
    session = CallParticipantFactory().session
    ChatMessageFactory(session=session)

    response = logged_in_client(user).get(call_url(session, "messages/"))

    assert response.status_code == 404


def test_call_messages_anonymous():
    """Anonymous users cannot read messages."""
    session = CallParticipantFactory().session

    assert APIClient().get(call_url(session, "messages/")).status_code == 401


# Read marker


def test_call_read():
    """Opening a call marks its messages as read."""
    user = UserFactory()
    session = CallSessionFactory()
    CallParticipantFactory(session=session, user=user, left_at=session.started_at)
    ChatMessageFactory(
        session=session, sent_at=session.started_at + timedelta(minutes=5)
    )
    client = logged_in_client(user)
    assert client.get("/api/v1.0/calls/").json()["results"][0]["unread_count"] == 1

    response = client.post(call_url(session, "read/"))

    assert response.status_code == 204
    assert CallUserState.objects.get(session=session, user=user).last_read_at
    assert client.get("/api/v1.0/calls/").json()["results"][0]["unread_count"] == 0

    # Again, later
    assert client.post(call_url(session, "read/")).status_code == 204
    assert CallUserState.objects.count() == 1


def test_call_read_not_visible():
    """Calls the user cannot see cannot be marked as read."""
    user = UserFactory()
    session = CallParticipantFactory().session

    response = logged_in_client(user).post(call_url(session, "read/"))

    assert response.status_code == 404
    assert not CallUserState.objects.exists()


def test_call_read_get_not_allowed():
    """The read marker is set with POST only."""
    user = UserFactory()
    session = CallParticipantFactory(user=user).session

    response = logged_in_client(user).get(call_url(session, "read/"))

    assert response.status_code == 405
