"""Test the list of the calls of the logged-in user."""

# pylint: disable=unused-argument

from datetime import timedelta

from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from core.factories import (
    RecordingFactory,
    RoomFactory,
    UserFactory,
    UserResourceAccessFactory,
)
from core.models import RecordingStatusChoices, RoleChoices

from calls.factories import (
    CallParticipantFactory,
    CallSessionFactory,
    ChatMessageFactory,
)
from calls.models import CallRoomTitle, CallUserState
from calls.tests.utils import logged_in_client
from transcripts.factories import TranscriptFactory
from transcripts.models import TranscriptStatusChoices

pytestmark = pytest.mark.django_db

LIST_URL = "/api/v1.0/calls/"


def iso(moment):
    """A datetime as the API writes it."""
    return moment.isoformat().replace("+00:00", "Z")


def list_calls(user, query=""):
    """List the calls of a user, return the response."""
    return logged_in_client(user).get(f"{LIST_URL}{query}")


def listed_ids(user, query=""):
    """Ids of the calls listed for a user."""
    response = list_calls(user, query)
    assert response.status_code == 200
    return [item["id"] for item in response.json()["results"]]


def test_list_calls_anonymous():
    """Anonymous users cannot list calls."""
    response = APIClient().get(LIST_URL)

    assert response.status_code == 401


def test_list_calls_disabled(settings):
    """The endpoint does not exist when the call history is disabled."""
    settings.CALLS_HISTORY_ENABLED = False
    user = UserFactory()
    CallParticipantFactory(user=user)

    assert list_calls(user).status_code == 404


def test_list_calls_item():
    """A call shows its room, title, people, last message and counters."""
    user = UserFactory()
    room = RoomFactory(name="abc-defg-hij")
    start = timezone.now().replace(microsecond=0) - timedelta(hours=2)
    end = start + timedelta(minutes=45)
    session = CallSessionFactory(
        room=room, title="Планёрка", started_at=start, ended_at=end
    )
    CallParticipantFactory(
        session=session,
        user=user,
        name="Иван Петров",
        left_at=start + timedelta(minutes=30),
    )
    CallParticipantFactory(
        session=session, name="Мария", joined_at=start + timedelta(minutes=1)
    )
    CallParticipantFactory(
        session=session, name="Гость", joined_at=start + timedelta(minutes=2)
    )
    ChatMessageFactory(
        session=session,
        sender_user=user,
        sender_name="Иван Петров",
        text="Начинаем",
        sent_at=start + timedelta(minutes=3),
    )
    # Sent after the user left: unread
    last = ChatMessageFactory(
        session=session,
        sender_name="Мария",
        text="Ссылка на док",
        sent_at=start + timedelta(minutes=40),
    )
    session.last_activity_at = last.sent_at
    session.save()

    response = list_calls(user)

    assert response.status_code == 200
    assert response.json() == {
        "count": 1,
        "next": None,
        "previous": None,
        "results": [
            {
                "id": str(session.id),
                "room": {"id": str(room.id), "slug": "abc-defg-hij"},
                "title": "Планёрка",
                "started_at": iso(start),
                "ended_at": iso(end),
                "is_active": False,
                "participants": [
                    {"name": "Иван Петров", "is_me": True},
                    {"name": "Мария", "is_me": False},
                    {"name": "Гость", "is_me": False},
                ],
                "participants_count": 3,
                "last_message": {
                    "sender_name": "Мария",
                    "text": "Ссылка на док",
                    "sent_at": iso(last.sent_at),
                    "is_mine": False,
                },
                "messages_count": 2,
                "unread_count": 1,
                "has_transcript": False,
                "has_recording": False,
            }
        ],
    }


def test_list_calls_active_without_messages():
    """An ongoing call without messages."""
    user = UserFactory()
    session = CallSessionFactory()
    CallParticipantFactory(session=session, user=user)

    item = list_calls(user).json()["results"][0]

    assert item["is_active"] is True
    assert item["ended_at"] is None
    assert item["last_message"] is None
    assert item["messages_count"] == 0
    assert item["unread_count"] == 0


def test_list_calls_own_last_message():
    """The last message of the user is marked as theirs."""
    user = UserFactory()
    session = CallSessionFactory()
    CallParticipantFactory(session=session, user=user)
    ChatMessageFactory(session=session, sender_user=user)

    item = list_calls(user).json()["results"][0]

    assert item["last_message"]["is_mine"] is True


def test_list_calls_last_message_preview_truncated():
    """Long last messages are cut in the list."""
    user = UserFactory()
    session = CallSessionFactory()
    CallParticipantFactory(session=session, user=user)
    ChatMessageFactory(session=session, text="я" * 2000)

    item = list_calls(user).json()["results"][0]

    assert item["last_message"]["text"] == "я" * 200


@pytest.mark.parametrize(
    "session_title, room_title, room_name, expected",
    [
        ("Планёрка", "Совещание", "Daily sync", "Планёрка"),
        ("", "Совещание", "Daily sync", "Совещание"),
        ("", None, "Daily sync", "Daily sync"),
        ("", None, "abc-defg-hij", ""),
    ],
)
def test_list_calls_title(session_title, room_title, room_name, expected):
    """The title falls back on the room call title, then on a named room."""
    user = UserFactory()
    room = RoomFactory(name=room_name)
    if room_title:
        CallRoomTitle.objects.create(room=room, title=room_title)
    session = CallSessionFactory(room=room, title=session_title)
    CallParticipantFactory(session=session, user=user)

    item = list_calls(user).json()["results"][0]

    assert item["title"] == expected


def test_list_calls_participants_capped():
    """The list shows the first participants; the count gives them all."""
    user = UserFactory()
    session = CallSessionFactory()
    start = session.started_at
    for index in range(9):
        CallParticipantFactory(
            session=session,
            name=f"Person {index}",
            joined_at=start + timedelta(minutes=index),
        )
    CallParticipantFactory(
        session=session, user=user, joined_at=start + timedelta(hours=1)
    )

    item = list_calls(user).json()["results"][0]

    assert [p["name"] for p in item["participants"]] == [
        f"Person {index}" for index in range(6)
    ]
    assert item["participants_count"] == 10


# Visibility


def test_list_calls_visibility():
    """Users see the calls they attended, and the ones of rooms they administrate."""
    user = UserFactory()
    attended = CallParticipantFactory(user=user).session
    CallParticipantFactory()  # someone else's call
    owned_room = RoomFactory(users=[(user, RoleChoices.OWNER)])
    owned = CallParticipantFactory(session__room=owned_room).session
    administrated_room = RoomFactory(users=[(user, RoleChoices.ADMIN)])
    administrated = CallParticipantFactory(session__room=administrated_room).session
    member_room = RoomFactory(users=[(user, RoleChoices.MEMBER)])
    CallParticipantFactory(session__room=member_room)

    assert sorted(listed_ids(user)) == sorted(
        [str(attended.id), str(owned.id), str(administrated.id)]
    )


def test_list_calls_room_admins_setting(settings):
    """Room owners only see the calls they attended when the setting is off."""
    settings.CALLS_VISIBLE_TO_ROOM_ADMINS = False
    user = UserFactory()
    room = RoomFactory(users=[(user, RoleChoices.OWNER)])
    attended = CallParticipantFactory(session__room=room, user=user).session
    CallParticipantFactory(session__room=room, session__ended_at=timezone.now())

    assert listed_ids(user) == [str(attended.id)]


def test_list_calls_guest_participation_not_linked():
    """A participation as a guest is not the user's."""
    user = UserFactory()
    CallParticipantFactory(identity=user.sub, user=None)

    assert listed_ids(user) == []


def test_list_calls_empty_calls_hidden():
    """Calls nobody joined are not listed, even to room owners."""
    user = UserFactory()
    room = RoomFactory(users=[(user, RoleChoices.OWNER)])
    CallSessionFactory(room=room, ended_at=timezone.now())
    with_message = CallSessionFactory(room=room)
    ChatMessageFactory(session=with_message)

    assert listed_ids(user) == [str(with_message.id)]


def test_list_calls_deleted_room():
    """Calls of a deleted room stay in the history."""
    user = UserFactory()
    session = CallParticipantFactory(user=user).session
    session.room.soft_delete()

    assert listed_ids(user) == [str(session.id)]


# Ordering, search and pagination


def test_list_calls_ordered_by_last_activity():
    """The calls with the latest activity come first."""
    user = UserFactory()
    now = timezone.now()
    calls = []
    for hours in (5, 1, 3):
        session = CallSessionFactory(
            started_at=now - timedelta(hours=10),
            last_activity_at=now - timedelta(hours=hours),
        )
        CallParticipantFactory(session=session, user=user)
        calls.append(session)

    assert listed_ids(user) == [str(calls[1].id), str(calls[2].id), str(calls[0].id)]


@pytest.mark.parametrize(
    "query, expected",
    [
        ("план", ["titled"]),
        ("ДОКУМЕНТ", ["messaged"]),
        ("Ольга", ["attended"]),
        ("daily", ["named"]),
        ("совет", ["room_titled"]),
        ("nothing matches", []),
        ("", ["titled", "messaged", "attended", "named", "room_titled"]),
    ],
)
def test_list_calls_search(query, expected):
    """Calls are searched in titles, rooms, participant names and messages."""
    user = UserFactory()
    now = timezone.now()
    calls = {}
    for index, key in enumerate(
        ["titled", "messaged", "attended", "named", "room_titled"]
    ):
        session = CallSessionFactory(
            room=RoomFactory(
                name="Daily sync" if key == "named" else f"abc-defg-hi{index}"
            ),
            title="Планёрка" if key == "titled" else "",
            last_activity_at=now - timedelta(minutes=index),
        )
        CallParticipantFactory(session=session, user=user, name="Иван")
        calls[key] = session
    CallParticipantFactory(session=calls["attended"], name="Ольга Смирнова")
    ChatMessageFactory(session=calls["messaged"], text="Вот документ")
    CallRoomTitle.objects.create(room=calls["room_titled"].room, title="Совет")

    ids = listed_ids(user, f"?q={query}")

    assert ids == [str(calls[key].id) for key in expected]


def test_list_calls_pagination():
    """The list is paginated."""
    user = UserFactory()
    for _index in range(3):
        CallParticipantFactory(user=user)

    response = list_calls(user, "?page_size=2")
    second = list_calls(user, "?page_size=2&page=2")

    assert response.json()["count"] == 3
    assert len(response.json()["results"]) == 2
    assert response.json()["next"] is not None
    assert len(second.json()["results"]) == 1


@pytest.mark.parametrize("calls_count", [1, 8])
def test_list_calls_query_count(calls_count, django_assert_num_queries):
    """Listing calls takes a fixed number of queries."""
    user = UserFactory()
    for _index in range(calls_count):
        session = CallSessionFactory()
        CallParticipantFactory(session=session, user=user)
        CallParticipantFactory(session=session)
        ChatMessageFactory(session=session)
        ChatMessageFactory(session=session)
        TranscriptFactory(
            room=session.room, started_at=session.started_at, users=[user]
        )
        RecordingFactory(room=session.room, users=[user])

    client = logged_in_client(user)
    # User (the session is cached), count, page, participants, last messages
    with django_assert_num_queries(5):
        response = client.get(LIST_URL)

    assert response.status_code == 200
    assert len(response.json()["results"]) == calls_count


# Unread messages


def test_list_calls_unread_count():
    """Messages of others sent after the user left, or last read, are unread."""
    user = UserFactory()
    session = CallSessionFactory()
    start = session.started_at
    CallParticipantFactory(
        session=session, user=user, left_at=start + timedelta(minutes=10)
    )
    ChatMessageFactory(session=session, sent_at=start + timedelta(minutes=5))
    ChatMessageFactory(session=session, sent_at=start + timedelta(minutes=15))
    ChatMessageFactory(session=session, sent_at=start + timedelta(minutes=25))
    ChatMessageFactory(
        session=session, sender_user=user, sent_at=start + timedelta(minutes=30)
    )

    assert list_calls(user).json()["results"][0]["unread_count"] == 2

    CallUserState.objects.create(
        session=session, user=user, last_read_at=start + timedelta(minutes=20)
    )
    assert list_calls(user).json()["results"][0]["unread_count"] == 1

    CallUserState.objects.filter(session=session, user=user).update(
        last_read_at=timezone.now()
    )
    assert list_calls(user).json()["results"][0]["unread_count"] == 0


def test_list_calls_unread_while_present():
    """Messages are read by a user who is in the call."""
    user = UserFactory()
    session = CallSessionFactory()
    CallParticipantFactory(session=session, user=user)
    ChatMessageFactory(session=session, sent_at=timezone.now() - timedelta(seconds=5))

    assert list_calls(user).json()["results"][0]["unread_count"] == 0


def test_list_calls_unread_room_owner_absent():
    """For a room owner who did not attend, every message of others is unread."""
    user = UserFactory()
    room = RoomFactory(users=[(user, RoleChoices.OWNER)])
    session = CallSessionFactory(room=room)
    CallParticipantFactory(session=session)
    ChatMessageFactory(
        session=session, sent_at=session.started_at - timedelta(seconds=30)
    )
    ChatMessageFactory(session=session)

    assert list_calls(user).json()["results"][0]["unread_count"] == 2


# Transcripts and recordings


def test_list_calls_has_transcript_participant(settings):
    """Participants see that a call was transcribed, when the setting allows it."""
    user = UserFactory()
    session = CallParticipantFactory(user=user).session
    TranscriptFactory(
        room=session.room,
        started_at=session.started_at + timedelta(minutes=1),
        status=TranscriptStatusChoices.READY,
    )

    assert list_calls(user).json()["results"][0]["has_transcript"] is True

    settings.CALLS_TRANSCRIPTS_VISIBLE_TO_PARTICIPANTS = False
    assert list_calls(user).json()["results"][0]["has_transcript"] is False


def test_list_calls_has_transcript_access(settings):
    """Users given access to a transcript see it, whatever the setting."""
    settings.CALLS_TRANSCRIPTS_VISIBLE_TO_PARTICIPANTS = False
    user = UserFactory()
    room = RoomFactory(users=[(user, RoleChoices.OWNER)])
    session = CallParticipantFactory(session__room=room).session
    TranscriptFactory(
        room=room, started_at=session.started_at + timedelta(minutes=1), users=[user]
    )

    assert list_calls(user).json()["results"][0]["has_transcript"] is True


def test_list_calls_has_transcript_room_owner_without_access():
    """A room owner who did not attend needs access to the transcript."""
    user = UserFactory()
    room = RoomFactory(users=[(user, RoleChoices.OWNER)])
    session = CallParticipantFactory(session__room=room).session
    TranscriptFactory(room=room, started_at=session.started_at + timedelta(minutes=1))

    assert list_calls(user).json()["results"][0]["has_transcript"] is False


@pytest.mark.parametrize(
    "offset, status, expected",
    [
        (timedelta(minutes=10), TranscriptStatusChoices.ACTIVE, True),
        (timedelta(seconds=-20), TranscriptStatusChoices.READY, True),
        (timedelta(minutes=10), TranscriptStatusChoices.FAILED, False),
        (timedelta(minutes=-5), TranscriptStatusChoices.READY, False),
        (timedelta(hours=2), TranscriptStatusChoices.READY, False),
    ],
)
def test_list_calls_has_transcript_window(offset, status, expected):
    """Only the transcripts started while the call was live belong to it."""
    user = UserFactory()
    session = CallSessionFactory(ended_at=timezone.now() - timedelta(minutes=30))
    CallParticipantFactory(session=session, user=user)
    TranscriptFactory(
        room=session.room, started_at=session.started_at + offset, status=status
    )
    TranscriptFactory(started_at=session.started_at + timedelta(minutes=10))

    assert list_calls(user).json()["results"][0]["has_transcript"] is expected


def test_list_calls_has_recording():
    """Only the recordings of the call the user can access are shown."""
    user = UserFactory()
    session = CallParticipantFactory(user=user).session
    RecordingFactory(room=session.room)

    assert list_calls(user).json()["results"][0]["has_recording"] is False

    RecordingFactory(
        room=session.room, users=[user], status=RecordingStatusChoices.SAVED
    )
    assert list_calls(user).json()["results"][0]["has_recording"] is True


def test_list_calls_has_recording_failed_or_outside():
    """Failed recordings and recordings of another call are not shown."""
    user = UserFactory()
    session = CallSessionFactory(ended_at=timezone.now() - timedelta(minutes=30))
    CallParticipantFactory(session=session, user=user)
    RecordingFactory(
        room=session.room, users=[user], status=RecordingStatusChoices.FAILED_TO_START
    )
    # Recorded during a later call of the room
    RecordingFactory(room=session.room, users=[user])

    assert list_calls(user).json()["results"][0]["has_recording"] is False


def test_list_calls_consecutive_calls():
    """A transcript or recording started at the end of a call is not shown
    with the next call of the room (CALLS-6).
    """
    user = UserFactory()
    room = RoomFactory()
    first_start = timezone.now().replace(microsecond=0) - timedelta(hours=2)
    first = CallSessionFactory(
        room=room,
        started_at=first_start,
        ended_at=first_start + timedelta(minutes=30),
    )
    second = CallSessionFactory(
        room=room, started_at=first.ended_at + timedelta(seconds=20)
    )
    CallParticipantFactory(session=second, user=user)
    TranscriptFactory(
        room=room,
        started_at=first.ended_at - timedelta(seconds=5),
        status=TranscriptStatusChoices.READY,
    )
    recording = RecordingFactory(room=room, users=[user])
    type(recording).objects.filter(pk=recording.pk).update(created_at=first.ended_at)

    item = list_calls(user).json()["results"][0]

    assert item["id"] == str(second.id)
    assert item["has_transcript"] is False
    assert item["has_recording"] is False


# Calls whose room_finished was lost


def test_list_calls_closes_calls_over(livekit):
    """Open calls that LiveKit no longer shows are listed as ended; live and
    recent calls stay open (CALLS-4).
    """
    user = UserFactory()
    now = timezone.now().replace(microsecond=0)
    over = CallSessionFactory(started_at=now - timedelta(days=2))
    CallParticipantFactory(
        session=over, user=user, left_at=over.started_at + timedelta(hours=1)
    )
    live = CallSessionFactory(started_at=now - timedelta(hours=1))
    CallParticipantFactory(session=live, user=user)
    recent = CallSessionFactory(started_at=now - timedelta(minutes=5))
    CallParticipantFactory(session=recent, user=user)
    livekit.start_room(live.room, sid=live.livekit_sid)

    items = {item["id"]: item for item in list_calls(user).json()["results"]}

    assert items[str(over.id)]["is_active"] is False
    assert items[str(over.id)]["ended_at"] == iso(over.started_at + timedelta(hours=1))
    assert items[str(live.id)]["is_active"] is True
    assert items[str(recent.id)]["is_active"] is True
    # Recent calls are not checked
    assert livekit.requests == [
        ("list_rooms", sorted([str(over.room_id), str(live.room_id)]))
    ]

    # Checked again a few minutes later at the earliest
    list_calls(user)
    assert len(livekit.requests) == 1


def test_list_calls_livekit_unavailable(livekit):
    """The list is shown as recorded when LiveKit does not answer."""
    user = UserFactory()
    session = CallSessionFactory()
    CallParticipantFactory(session=session, user=user)

    response = list_calls(user)

    assert response.status_code == 200
    assert response.json()["results"][0]["is_active"] is True
    assert len(livekit.requests) == 1


def test_list_calls_query_count_checked_with_livekit(
    livekit, django_assert_num_queries
):
    """Checking open calls with LiveKit adds no query while they are live."""
    user = UserFactory()
    for _index in range(3):
        session = CallSessionFactory()
        CallParticipantFactory(session=session, user=user)
        livekit.start_room(session.room, sid=session.livekit_sid)

    client = logged_in_client(user)
    with django_assert_num_queries(5):
        response = client.get(LIST_URL)

    assert len(response.json()["results"]) == 3
    assert len(livekit.requests) == 1


def test_list_calls_room_access_role_changes():
    """Visibility follows the current room roles."""
    user = UserFactory()
    room = RoomFactory()
    session = CallParticipantFactory(session__room=room).session
    assert listed_ids(user) == []

    UserResourceAccessFactory(resource=room, user=user, role=RoleChoices.ADMIN)
    assert listed_ids(user) == [str(session.id)]
