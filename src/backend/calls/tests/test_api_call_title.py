"""Test the title given to the calls of a room."""

# pylint: disable=unused-argument

import pytest
from rest_framework.test import APIClient

from core.factories import RoomFactory, UserFactory
from core.models import RoleChoices

from calls.models import CallRoomTitle, CallSession
from calls.tests.utils import logged_in_client, post_webhook, webhook_event

pytestmark = pytest.mark.django_db


def put_title(client, room, title):
    """Set the call title of a room."""
    return client.put(
        f"/api/v1.0/rooms/{room.id}/call-title/", {"title": title}, format="json"
    )


@pytest.mark.parametrize("role", [RoleChoices.OWNER, RoleChoices.ADMIN])
def test_call_title_set(role):
    """Room owners and administrators title the next calls of their room."""
    user = UserFactory()
    room = RoomFactory(name="abc-defg-hij", users=[(user, role)])

    response = put_title(logged_in_client(user), room, "  Планёрка по проекту  ")

    assert response.status_code == 200
    assert response.json() == {"title": "Планёрка по проекту"}
    call_title = CallRoomTitle.objects.get(room=room)
    assert call_title.title == "Планёрка по проекту"
    assert call_title.updated_by == user
    room.refresh_from_db()
    # The room keeps its name, which gives it its URL
    assert room.name == room.slug == "abc-defg-hij"

    post_webhook(webhook_event("room_started", room.id))
    assert CallSession.objects.get().title == "Планёрка по проекту"


def test_call_title_update_and_clear():
    """A title can be changed, and cleared with an empty string."""
    user = UserFactory()
    room = RoomFactory(users=[(user, RoleChoices.OWNER)])
    client = logged_in_client(user)

    put_title(client, room, "Первое")
    put_title(client, room, "Второе")
    assert CallRoomTitle.objects.get(room=room).title == "Второе"

    response = put_title(client, room, "")

    assert response.status_code == 200
    assert response.json() == {"title": ""}
    assert not CallRoomTitle.objects.exists()


def test_call_title_member():
    """Room members cannot title the calls."""
    user = UserFactory()
    room = RoomFactory(users=[(user, RoleChoices.MEMBER)])

    response = put_title(logged_in_client(user), room, "Планёрка")

    assert response.status_code == 403
    assert not CallRoomTitle.objects.exists()


def test_call_title_stranger():
    """Users without role in the room cannot title its calls."""
    room = RoomFactory()

    response = put_title(logged_in_client(UserFactory()), room, "Планёрка")

    assert response.status_code == 403


def test_call_title_anonymous():
    """Anonymous users cannot title calls."""
    room = RoomFactory()

    response = put_title(APIClient(), room, "Планёрка")

    assert response.status_code == 401


def test_call_title_too_long():
    """Titles are limited to 255 characters."""
    user = UserFactory()
    room = RoomFactory(users=[(user, RoleChoices.OWNER)])

    response = put_title(logged_in_client(user), room, "x" * 256)

    assert response.status_code == 400
    assert "title" in response.json()


def test_call_title_missing():
    """The title is required (an empty string clears it)."""
    user = UserFactory()
    room = RoomFactory(users=[(user, RoleChoices.OWNER)])

    response = logged_in_client(user).put(
        f"/api/v1.0/rooms/{room.id}/call-title/", {}, format="json"
    )

    assert response.status_code == 400


def test_call_title_deleted_room():
    """Deleted rooms cannot be titled."""
    user = UserFactory()
    room = RoomFactory(users=[(user, RoleChoices.OWNER)])
    room.soft_delete()

    response = put_title(logged_in_client(user), room, "Планёрка")

    assert response.status_code == 410


def test_call_title_unknown_room():
    """Unknown rooms are not found."""
    user = UserFactory()

    response = logged_in_client(user).put(
        "/api/v1.0/rooms/7c1c0d3e-1111-4a5b-9c8d-0e0f00000000/call-title/",
        {"title": "Планёрка"},
        format="json",
    )

    assert response.status_code == 404


def test_call_title_disabled(settings):
    """The endpoint does not exist when the call history is disabled."""
    settings.CALLS_HISTORY_ENABLED = False
    user = UserFactory()
    room = RoomFactory(users=[(user, RoleChoices.OWNER)])

    response = put_title(logged_in_client(user), room, "Планёрка")

    assert response.status_code == 404
