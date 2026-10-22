"""Test the LiveKit webhook endpoint of the calls application."""

# pylint: disable=unused-argument

import json
from datetime import timedelta

from django.utils import timezone

import pytest
from rest_framework.test import APIClient

from core.factories import RoomFactory, UserFactory

from calls.factories import (
    CallParticipantFactory,
    CallSessionFactory,
)
from calls.models import CallParticipant, CallRoomTitle, CallSession
from calls.tests.utils import (
    CALL_START,
    WEBHOOK_URL,
    participant_info,
    post_webhook,
    sign_webhook,
    webhook_event,
)

pytestmark = pytest.mark.django_db


def test_webhook_disabled(settings):
    """The endpoint does not exist when the call history is disabled."""
    settings.CALLS_HISTORY_ENABLED = False
    room = RoomFactory()

    response = post_webhook(webhook_event("room_started", room.id))

    assert response.status_code == 404
    assert not CallSession.objects.exists()


# Signature


def test_webhook_missing_signature():
    """A webhook without Authorization header is refused with a 401."""
    room = RoomFactory()

    response = APIClient().post(
        WEBHOOK_URL,
        data=json.dumps(webhook_event("room_started", room.id)),
        content_type="application/webhook+json",
    )

    assert response.status_code == 401
    assert response.json() == {"status": "error"}
    assert not CallSession.objects.exists()


def test_webhook_garbage_signature():
    """A webhook whose Authorization header is not a JWT is refused with a 401."""
    room = RoomFactory()

    response = post_webhook(
        webhook_event("room_started", room.id), authorization="not-a-token"
    )

    assert response.status_code == 401
    assert not CallSession.objects.exists()


def test_webhook_signed_with_another_secret(settings):
    """A webhook signed with another API secret is refused with a 401."""
    room = RoomFactory()
    body = json.dumps(webhook_event("room_started", room.id))
    real_configuration = settings.LIVEKIT_CONFIGURATION
    settings.LIVEKIT_CONFIGURATION = {
        **real_configuration,
        "api_secret": "another-secret-padded-for-minimum-len!",
    }
    authorization = sign_webhook(body)
    settings.LIVEKIT_CONFIGURATION = real_configuration

    response = post_webhook(json.loads(body), authorization=authorization)

    assert response.status_code == 401
    assert not CallSession.objects.exists()


def test_webhook_body_does_not_match_signature():
    """A body changed after signing is refused with a 401."""
    room = RoomFactory()
    authorization = sign_webhook(json.dumps(webhook_event("room_started", room.id)))

    response = post_webhook(
        webhook_event("room_started", room.id, sid="RM_forged"),
        authorization=authorization,
    )

    assert response.status_code == 401
    assert not CallSession.objects.exists()


def test_webhook_signed_invalid_payload():
    """A signed body that is not a LiveKit event is refused with a 400."""
    response = post_webhook({"event": {"unexpected": "object"}})

    assert response.status_code == 400
    assert response.json() == {"status": "error"}


def test_webhook_unknown_event_type():
    """Events the calls do not need are acknowledged."""
    room = RoomFactory()

    response = post_webhook(webhook_event("track_published", room.id))

    assert response.status_code == 200
    assert response.json() == {"status": "success"}
    assert not CallSession.objects.exists()


# Ignored rooms


@pytest.mark.parametrize(
    "room_name",
    [
        "connection-test-0f2c4cbb-1d3e-4b0e-9a42-7f0d6a3b1a10",
        "not-a-uuid",
        "abc-defg-hij",
        "0f2c4cbb-1d3e-4b0e-9a42-7f0d6a3b1a10",  # unknown room
    ],
)
def test_webhook_ignored_rooms(room_name):
    """Events of rooms that are not ours are acknowledged and ignored."""
    for event in ("room_started", "participant_joined", "room_finished"):
        response = post_webhook(
            webhook_event(event, room_name, participant=participant_info("guest-1"))
        )

        assert response.status_code == 200

    assert not CallSession.objects.exists()


def test_webhook_filter_regex(settings):
    """Rooms filtered out by LIVEKIT_WEBHOOK_EVENTS_FILTER_REGEX are ignored."""
    settings.LIVEKIT_WEBHOOK_EVENTS_FILTER_REGEX = r"^ffffffff-"
    room = RoomFactory()

    response = post_webhook(webhook_event("room_started", room.id))

    assert response.status_code == 200
    assert not CallSession.objects.exists()


def test_webhook_deleted_room_not_opened():
    """A deleted room brought back to life by an old token opens no call."""
    room = RoomFactory()
    room.soft_delete()

    for event in ("room_started", "participant_joined"):
        response = post_webhook(
            webhook_event(event, room.id, participant=participant_info("guest-1"))
        )
        assert response.status_code == 200

    assert not CallSession.objects.exists()


def test_webhook_deleted_room_call_still_closed():
    """A call whose room was deleted meanwhile is still closed."""
    room = RoomFactory()
    session = CallSessionFactory(room=room, livekit_sid="RM_first")
    participant = CallParticipantFactory(session=session, identity="guest-1")
    room.soft_delete()

    end = session.started_at + timedelta(minutes=30)
    post_webhook(
        webhook_event(
            "participant_left",
            room.id,
            at=end,
            participant=participant_info("guest-1", joined_at=participant.joined_at),
        )
    )
    post_webhook(webhook_event("room_finished", room.id, at=end))

    session.refresh_from_db()
    participant.refresh_from_db()
    assert session.ended_at == end
    assert participant.left_at == end


# Sessions


def test_webhook_room_started_opens_call():
    """room_started opens a call, dated by the LiveKit room creation time."""
    room = RoomFactory()
    CallRoomTitle.objects.create(room=room, title="Планёрка")
    created_at = CALL_START + timedelta(milliseconds=250)

    response = post_webhook(
        webhook_event(
            "room_started",
            room.id,
            room_created_at=created_at,
            at=CALL_START + timedelta(seconds=1),
        )
    )

    assert response.status_code == 200
    session = CallSession.objects.get()
    assert session.room == room
    assert session.livekit_sid == "RM_first"
    assert session.title == "Планёрка"
    assert session.started_at == created_at
    assert session.last_activity_at == created_at
    assert session.ended_at is None
    assert session.is_active


def test_webhook_replays_are_idempotent():
    """LiveKit retries an event it could not deliver: replays change nothing."""
    room = RoomFactory()
    user = UserFactory()
    joined = participant_info(
        user.sub, joined_at=CALL_START + timedelta(seconds=5), is_authenticated=True
    )
    left_at = CALL_START + timedelta(minutes=20)
    events = [
        webhook_event("room_started", room.id, event_id="EV_1"),
        webhook_event(
            "participant_joined",
            room.id,
            participant=joined,
            at=CALL_START + timedelta(seconds=5),
            event_id="EV_2",
        ),
        webhook_event(
            "participant_left",
            room.id,
            participant=joined,
            at=left_at,
            event_id="EV_3",
        ),
        webhook_event("room_finished", room.id, at=left_at + timedelta(seconds=20)),
    ]

    for event in events:
        for _replay in range(2):
            assert post_webhook(event).status_code == 200

    session = CallSession.objects.get()
    assert session.ended_at == left_at + timedelta(seconds=20)
    participant = CallParticipant.objects.get()
    assert participant.session == session
    assert participant.user == user
    assert participant.is_guest is False
    assert participant.joined_at == CALL_START + timedelta(seconds=5)
    assert participant.left_at == left_at


def test_webhook_participant_joined_before_room_started():
    """A participant event opens the call if room_started was not processed yet."""
    room = RoomFactory()

    post_webhook(
        webhook_event(
            "participant_joined",
            room.id,
            participant=participant_info("guest-1"),
            at=CALL_START + timedelta(seconds=3),
        )
    )
    post_webhook(webhook_event("room_started", room.id))

    session = CallSession.objects.get()
    assert session.started_at == CALL_START
    assert session.participants.get().identity == "guest-1"


def test_webhook_earlier_creation_time_moves_start():
    """A call opened by an event without the room creation time starts when
    LiveKit created the room.
    """
    room = RoomFactory()
    session = CallSessionFactory(
        room=room, livekit_sid="RM_first", started_at=CALL_START + timedelta(minutes=2)
    )

    post_webhook(webhook_event("room_started", room.id))

    session.refresh_from_db()
    assert session.started_at == CALL_START


def test_webhook_lost_room_finished_closed_by_next_call():
    """When room_finished was lost, the next call of the room closes the call."""
    room = RoomFactory()
    previous = CallSessionFactory(room=room, livekit_sid="RM_old")
    left = CallParticipantFactory(
        session=previous,
        identity="guest-1",
        left_at=previous.started_at + timedelta(minutes=40),
    )
    still_present = CallParticipantFactory(
        session=previous,
        identity="guest-2",
        last_joined_at=previous.started_at + timedelta(minutes=10),
    )
    next_start = previous.started_at + timedelta(hours=3)

    post_webhook(webhook_event("room_started", room.id, room_created_at=next_start))

    previous.refresh_from_db()
    still_present.refresh_from_db()
    left.refresh_from_db()
    assert previous.ended_at == previous.started_at + timedelta(minutes=40)
    assert left.left_at == previous.started_at + timedelta(minutes=40)
    assert still_present.left_at == previous.ended_at
    current = CallSession.objects.get(livekit_sid="RM_first")
    assert current.started_at == next_start
    assert current.ended_at is None


def test_webhook_lost_room_finished_of_empty_call():
    """A call nobody joined, whose room_finished was lost, is deleted."""
    room = RoomFactory()
    previous = CallSessionFactory(room=room, livekit_sid="RM_old")

    post_webhook(webhook_event("room_started", room.id))

    assert not CallSession.objects.filter(pk=previous.pk).exists()
    assert CallSession.objects.get().livekit_sid == "RM_first"


def test_webhook_room_finished_closes_call():
    """room_finished ends the call and its participants still present."""
    room = RoomFactory()
    session = CallSessionFactory(room=room, livekit_sid="RM_first")
    present = CallParticipantFactory(session=session, identity="guest-1")
    end = session.started_at + timedelta(minutes=30)

    response = post_webhook(webhook_event("room_finished", room.id, at=end))

    assert response.status_code == 200
    session.refresh_from_db()
    present.refresh_from_db()
    assert session.ended_at == end
    assert not session.is_active
    assert present.left_at == end


def test_webhook_room_finished_deletes_empty_call():
    """A call nobody joined (agents and egress do not count) is deleted."""
    room = RoomFactory()
    post_webhook(webhook_event("room_started", room.id))
    post_webhook(
        webhook_event(
            "participant_joined",
            room.id,
            participant=participant_info("agent-1", kind="AGENT"),
        )
    )

    post_webhook(
        webhook_event("room_finished", room.id, at=CALL_START + timedelta(minutes=5))
    )

    assert not CallSession.objects.exists()


def test_webhook_room_finished_unknown_call():
    """room_finished of a call that was never recorded is acknowledged."""
    room = RoomFactory()
    other = CallSessionFactory(room=room, livekit_sid="RM_other")

    response = post_webhook(webhook_event("room_finished", room.id))

    assert response.status_code == 200
    other.refresh_from_db()
    assert other.ended_at is None


# Participants


@pytest.mark.parametrize("kind", ["AGENT", "EGRESS", "INGRESS"])
def test_webhook_non_human_participants_ignored(kind):
    """Agents, egress and ingress participants are not participants of the call."""
    room = RoomFactory()
    post_webhook(webhook_event("room_started", room.id))

    for event in ("participant_joined", "participant_left"):
        response = post_webhook(
            webhook_event(
                event, room.id, participant=participant_info("bot", kind=kind)
            )
        )
        assert response.status_code == 200

    assert not CallParticipant.objects.exists()


def test_webhook_sip_participant_recorded():
    """Phone callers are participants (guests)."""
    room = RoomFactory()

    post_webhook(
        webhook_event(
            "participant_joined",
            room.id,
            participant=participant_info("sip_+33600000000", kind="SIP", name="Phone"),
        )
    )

    participant = CallParticipant.objects.get()
    assert participant.name == "Phone"
    assert participant.is_guest
    assert participant.user is None


def test_webhook_guest_never_linked_to_a_user():
    """An identity is linked to a user only for authenticated participants."""
    user = UserFactory()
    room = RoomFactory()

    post_webhook(
        webhook_event(
            "participant_joined",
            room.id,
            participant=participant_info(user.sub, is_authenticated=False),
        )
    )

    participant = CallParticipant.objects.get()
    assert participant.user is None
    assert participant.is_guest


def test_webhook_participant_rejoins():
    """A participant who leaves and comes back is present again, with one row."""
    room = RoomFactory()
    first_join = CALL_START + timedelta(seconds=2)
    first_left = CALL_START + timedelta(minutes=5)
    second_join = CALL_START + timedelta(minutes=6, milliseconds=500)
    second_left = CALL_START + timedelta(minutes=50)

    def send(event, joined_at, at):
        post_webhook(
            webhook_event(
                event,
                room.id,
                at=at,
                participant=participant_info(
                    "guest-1", joined_at=joined_at, name=f"Guest {event}"
                ),
            )
        )

    send("participant_joined", first_join, first_join)
    send("participant_connection_aborted", first_join, first_left)
    participant = CallParticipant.objects.get()
    assert participant.left_at == first_left

    send("participant_joined", second_join, second_join)
    participant.refresh_from_db()
    assert participant.joined_at == first_join
    assert participant.last_joined_at == second_join
    assert participant.left_at is None

    send("participant_left", second_join, second_left)
    participant.refresh_from_db()
    assert participant.left_at == second_left
    assert participant.name == "Guest participant_joined"


def test_webhook_previous_connection_left_after_new_one_joined():
    """Joining from another tab disconnects the previous connection: its
    departure, if it comes after the new join, does not mark the person gone.
    """
    room = RoomFactory()
    first_join = CALL_START
    second_join = CALL_START + timedelta(minutes=10)

    post_webhook(
        webhook_event(
            "participant_joined",
            room.id,
            participant=participant_info("guest-1", joined_at=first_join),
        )
    )
    post_webhook(
        webhook_event(
            "participant_joined",
            room.id,
            at=second_join,
            participant=participant_info("guest-1", joined_at=second_join),
        )
    )
    post_webhook(
        webhook_event(
            "participant_left",
            room.id,
            at=second_join + timedelta(seconds=1),
            participant=participant_info("guest-1", joined_at=first_join),
        )
    )

    participant = CallParticipant.objects.get()
    assert participant.left_at is None


def test_webhook_participant_left_unknown_call():
    """The departure from a call that was never recorded is acknowledged."""
    room = RoomFactory()

    response = post_webhook(
        webhook_event("participant_left", room.id, participant=participant_info("x"))
    )

    assert response.status_code == 200
    assert not CallSession.objects.exists()


def test_webhook_participant_left_without_timestamps():
    """A departure without join time still closes the participation."""
    room = RoomFactory()
    session = CallSessionFactory(room=room, livekit_sid="RM_first")
    participant = CallParticipantFactory(session=session, identity="guest-1")
    info = participant_info("guest-1")
    del info["joinedAt"]
    del info["joinedAtMs"]
    left_at = timezone.now().replace(microsecond=0)

    post_webhook(
        webhook_event("participant_left", room.id, participant=info, at=left_at)
    )

    participant.refresh_from_db()
    assert participant.left_at == left_at
