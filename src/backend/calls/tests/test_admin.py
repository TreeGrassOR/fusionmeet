"""Test the admin of the calls application."""

from django.test import Client

import pytest

from core.factories import RoomFactory, UserFactory

from calls.factories import (
    CallParticipantFactory,
    CallSessionFactory,
    ChatMessageFactory,
)
from calls.models import CallRoomTitle, CallUserState

pytestmark = pytest.mark.django_db


@pytest.fixture(name="admin_client")
def fixture_admin_client(settings):
    """A client logged in as a superuser.

    Admin pages link static files: their manifest is not built for tests.
    """
    settings.STORAGES = {
        **settings.STORAGES,
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
        },
    }
    client = Client()
    client.force_login(UserFactory(is_staff=True, is_superuser=True))
    return client


def test_admin_calls_read_only(admin_client):
    """Calls, with their participants and messages, can be viewed, not edited."""
    session = CallSessionFactory()
    CallParticipantFactory(session=session, name="Иван")
    ChatMessageFactory(session=session, text="Привет из чата")

    changelist = admin_client.get("/admin/calls/callsession/")
    change = admin_client.get(f"/admin/calls/callsession/{session.id}/change/")
    add = admin_client.get("/admin/calls/callsession/add/")
    edit = admin_client.post(
        f"/admin/calls/callsession/{session.id}/change/", {"title": "Changed"}
    )

    assert changelist.status_code == 200
    assert change.status_code == 200
    content = change.content.decode()
    assert "Иван" in content
    assert "Привет из чата" in content
    assert add.status_code == 403
    assert edit.status_code == 403
    session.refresh_from_db()
    assert session.title == ""


def test_admin_room_call_titles_read_only(admin_client):
    """Room call titles can be viewed, not edited."""
    title = CallRoomTitle.objects.create(room=RoomFactory(), title="Планёрка")

    changelist = admin_client.get("/admin/calls/callroomtitle/")
    change = admin_client.get(f"/admin/calls/callroomtitle/{title.pk}/change/")
    add = admin_client.get("/admin/calls/callroomtitle/add/")

    assert changelist.status_code == 200
    assert "Планёрка" in changelist.content.decode()
    assert change.status_code == 200
    assert add.status_code == 403


def test_models_str_titles_and_states():
    """Read states and room titles have readable representations."""
    user = UserFactory()
    session = CallSessionFactory()
    state = CallUserState.objects.create(session=session, user=user)
    title = CallRoomTitle.objects.create(room=session.room, title="Планёрка")

    assert str(state) == f"Read state of user {user.id} in call {session.id}"
    assert str(title) == "Планёрка"
