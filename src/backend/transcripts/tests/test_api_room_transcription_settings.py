"""Test the endpoint choosing the recognition model of a room."""

# pylint: disable=redefined-outer-name,unused-argument

import copy
from unittest import mock

import pytest
import requests
from rest_framework.test import APIClient

from core.factories import RoomFactory, UserFactory
from core.models import RoleChoices, RoomAccessLevel

from transcripts.models import RoomTranscriptionSettings
from transcripts.tests.utils import ASR_MODELS_PAYLOAD, ASR_MODELS_URL

pytestmark = pytest.mark.django_db


def settings_url(room):
    """URL of the transcription settings of a room."""
    return f"/api/v1.0/rooms/{room.id}/transcription-settings/"


def owner_client(room, role=RoleChoices.OWNER):
    """Return a client logged in as a user with a role in the room, and the user."""
    user = UserFactory()
    room.accesses.create(user=user, role=role)
    client = APIClient()
    client.force_login(user)
    return client, user


def put_model(client, room, asr_model):
    """Choose the recognition model of a room."""
    return client.put(settings_url(room), {"asr_model": asr_model}, format="json")


# Permissions


@pytest.mark.parametrize("enabled, expected_status", [(True, 401), (False, 404)])
def test_transcription_settings_anonymous(
    settings, asr_service, enabled, expected_status
):
    """Anonymous users can neither read nor change the settings."""
    settings.TRANSCRIPTION_ENABLED = enabled
    room = RoomFactory()

    assert APIClient().get(settings_url(room)).status_code == expected_status
    response = put_model(APIClient(), room, "gigaam-multilingual-punct")

    assert response.status_code == expected_status
    assert RoomTranscriptionSettings.objects.count() == 0


def test_transcription_settings_disabled(settings, asr_service):
    """The endpoint does not exist when the transcription is disabled."""
    settings.TRANSCRIPTION_ENABLED = False
    room = RoomFactory()
    client, _ = owner_client(room)

    assert client.get(settings_url(room)).status_code == 404
    assert put_model(client, room, "gigaam-multilingual-punct").status_code == 404
    assert RoomTranscriptionSettings.objects.count() == 0
    assert len(asr_service.calls) == 0


@pytest.mark.parametrize("authenticated_participants_enabled", [True, False])
def test_transcription_settings_restricted_room_member(
    transcription_enabled, asr_service, authenticated_participants_enabled
):
    """Members of a restricted room without owner/administrator role get a 403."""
    transcription_enabled.RECORDING_AUTHENTICATED_PARTICIPANTS_ENABLED = (
        authenticated_participants_enabled
    )
    room = RoomFactory(access_level=RoomAccessLevel.RESTRICTED)
    client, _ = owner_client(room, role=RoleChoices.MEMBER)

    assert client.get(settings_url(room)).status_code == 403
    response = put_model(client, room, "gigaam-multilingual-punct")

    assert response.status_code == 403
    assert RoomTranscriptionSettings.objects.count() == 0


@mock.patch(
    "core.services.participants_management.ParticipantsManagement.check_if_in_meeting",
    return_value=False,
)
def test_transcription_settings_open_room_absent_user(
    mock_check, transcription_enabled, asr_service
):
    """An authenticated user who is not in the meeting gets a 403."""
    room = RoomFactory(access_level=RoomAccessLevel.PUBLIC)
    client = APIClient()
    client.force_login(UserFactory())

    response = put_model(client, room, "gigaam-multilingual-punct")

    assert response.status_code == 403
    assert RoomTranscriptionSettings.objects.count() == 0


@mock.patch(
    "core.services.participants_management.ParticipantsManagement.check_if_in_meeting",
    return_value=True,
)
def test_transcription_settings_open_room_present_user(
    mock_check, transcription_enabled, asr_service
):
    """Like the start of a transcription, present participants of an open room can."""
    room = RoomFactory(access_level=RoomAccessLevel.TRUSTED)
    user = UserFactory()
    client = APIClient()
    client.force_login(user)

    response = put_model(client, room, "gigaam-multilingual-punct")

    assert response.status_code == 200
    assert RoomTranscriptionSettings.objects.get(room=room).updated_by == user


def test_transcription_settings_deleted_room(transcription_enabled, asr_service):
    """Soft-deleted rooms cannot be configured."""
    room = RoomFactory()
    client, _ = owner_client(room)
    room.soft_delete()

    assert client.get(settings_url(room)).status_code == 410
    assert put_model(client, room, "gigaam-multilingual-punct").status_code == 410
    assert RoomTranscriptionSettings.objects.count() == 0


def test_transcription_settings_unknown_room(transcription_enabled, asr_service):
    """Unknown rooms give a 404."""
    client = APIClient()
    client.force_login(UserFactory())

    response = client.get(
        "/api/v1.0/rooms/7b8e3a3c-5d2a-4b1a-9c3e-0d6f1e2a3b4c/transcription-settings/"
    )

    assert response.status_code == 404


# Read


@pytest.mark.parametrize("role", [RoleChoices.OWNER, RoleChoices.ADMIN])
def test_transcription_settings_get_default(transcription_enabled, asr_service, role):
    """A room without settings uses the default model: null."""
    room = RoomFactory()
    client, _ = owner_client(room, role=role)

    response = client.get(settings_url(room))

    assert response.status_code == 200
    assert response.json() == {"asr_model": None}
    assert len(asr_service.calls) == 0


def test_transcription_settings_get_chosen(transcription_enabled, asr_service):
    """The model chosen for the room is returned, without calling the ASR service."""
    room = RoomFactory()
    RoomTranscriptionSettings.objects.create(
        room=room, asr_model="gigaam-multilingual-punct"
    )
    RoomTranscriptionSettings.objects.create(room=RoomFactory(), asr_model="gigaam-v3")
    client, _ = owner_client(room)

    response = client.get(settings_url(room))

    assert response.status_code == 200
    assert response.json() == {"asr_model": "gigaam-multilingual-punct"}
    assert len(asr_service.calls) == 0


def test_transcription_settings_get_reset(transcription_enabled, asr_service):
    """A room reset to the default model gives null."""
    room = RoomFactory()
    RoomTranscriptionSettings.objects.create(room=room, asr_model="")
    client, _ = owner_client(room)

    response = client.get(settings_url(room))

    assert response.json() == {"asr_model": None}


# Update


@pytest.mark.parametrize("role", [RoleChoices.OWNER, RoleChoices.ADMIN])
def test_transcription_settings_put(transcription_enabled, asr_service, role):
    """Owners and administrators choose a model listed by the ASR service."""
    room = RoomFactory()
    client, user = owner_client(room, role=role)

    response = put_model(client, room, "gigaam-multilingual-punct")

    assert response.status_code == 200
    assert response.json() == {"asr_model": "gigaam-multilingual-punct"}
    room_settings = RoomTranscriptionSettings.objects.get()
    assert room_settings.room == room
    assert room_settings.asr_model == "gigaam-multilingual-punct"
    assert room_settings.updated_by == user
    assert client.get(settings_url(room)).json() == {
        "asr_model": "gigaam-multilingual-punct"
    }


def test_transcription_settings_put_change(transcription_enabled, asr_service):
    """Choosing another model updates the settings of the room."""
    room = RoomFactory()
    first_client, _ = owner_client(room)
    second_client, second_user = owner_client(room)

    assert put_model(first_client, room, "gigaam-multilingual-punct").status_code == 200
    response = put_model(second_client, room, "gigaam-v3")

    assert response.status_code == 200
    assert response.json() == {"asr_model": "gigaam-v3"}
    room_settings = RoomTranscriptionSettings.objects.get()
    assert room_settings.asr_model == "gigaam-v3"
    assert room_settings.updated_by == second_user
    # The list of models was read once, then cached
    assert len(asr_service.calls) == 1


def test_transcription_settings_put_null(transcription_enabled, asr_service):
    """Null resets the room to the default model, without calling the ASR service."""
    room = RoomFactory()
    RoomTranscriptionSettings.objects.create(
        room=room, asr_model="gigaam-multilingual-punct"
    )
    client, user = owner_client(room)
    transcription_enabled.TRANSCRIPTION_ASR_URL = None

    response = put_model(client, room, None)

    assert response.status_code == 200
    assert response.json() == {"asr_model": None}
    room_settings = RoomTranscriptionSettings.objects.get()
    assert room_settings.asr_model == ""
    assert room_settings.updated_by == user
    assert client.get(settings_url(room)).json() == {"asr_model": None}
    assert len(asr_service.calls) == 0


def test_transcription_settings_put_loading_model(transcription_enabled, asr_service):
    """A model still loading can be chosen: it is ready within minutes."""
    payload = copy.deepcopy(ASR_MODELS_PAYLOAD)
    payload["models"][1]["status"] = "loading"
    asr_service.replace("GET", ASR_MODELS_URL, json=payload)
    room = RoomFactory()
    client, _ = owner_client(room)

    response = put_model(client, room, "gigaam-multilingual-punct")

    assert response.status_code == 200
    assert (
        RoomTranscriptionSettings.objects.get().asr_model == "gigaam-multilingual-punct"
    )


@pytest.mark.parametrize(
    "asr_model, error",
    [
        ("unknown-model", "Unknown recognition model."),
        ("GIGAAM-V3", "Unknown recognition model."),
        (
            "gigaam-multilingual",
            "This recognition model is unavailable: it failed to load.",
        ),
    ],
)
def test_transcription_settings_put_invalid_model(
    transcription_enabled, asr_service, asr_model, error
):
    """Unknown and failed models are refused."""
    room = RoomFactory()
    RoomTranscriptionSettings.objects.create(
        room=room, asr_model="gigaam-multilingual-punct"
    )
    client, _ = owner_client(room)

    response = put_model(client, room, asr_model)

    assert response.status_code == 400
    assert response.json() == {"asr_model": [error]}
    assert (
        RoomTranscriptionSettings.objects.get().asr_model == "gigaam-multilingual-punct"
    )


@pytest.mark.parametrize(
    "payload",
    [{}, {"asr_model": ""}, {"asr_model": "a" * 65}, {"asr_model": ["gigaam-v3"]}],
)
def test_transcription_settings_put_invalid_payload(
    transcription_enabled, asr_service, payload
):
    """Missing, empty, too long and non-string values are refused."""
    room = RoomFactory()
    client, _ = owner_client(room)

    response = client.put(settings_url(room), payload, format="json")

    assert response.status_code == 400
    assert "asr_model" in response.json()
    assert RoomTranscriptionSettings.objects.count() == 0
    assert len(asr_service.calls) == 0


@pytest.mark.parametrize(
    "answer",
    [
        {"body": requests.ConnectionError("refused")},
        {"status": 500, "body": "Internal Server Error"},
        {"status": 200, "json": {"models": None}},
    ],
)
def test_transcription_settings_put_asr_unavailable(
    transcription_enabled, asr_service, answer
):
    """A model cannot be chosen while the list of models is unavailable."""
    asr_service.replace("GET", ASR_MODELS_URL, **answer)
    room = RoomFactory()
    client, _ = owner_client(room)

    response = put_model(client, room, "gigaam-multilingual-punct")

    assert response.status_code == 503
    assert response.json() == {"detail": "The recognition models are unavailable."}
    assert RoomTranscriptionSettings.objects.count() == 0


def test_transcription_settings_put_asr_not_configured(
    transcription_enabled, asr_service
):
    """A model cannot be chosen when TRANSCRIPTION_ASR_URL is not set."""
    transcription_enabled.TRANSCRIPTION_ASR_URL = None
    room = RoomFactory()
    client, _ = owner_client(room)

    response = put_model(client, room, "gigaam-multilingual-punct")

    assert response.status_code == 503
    assert RoomTranscriptionSettings.objects.count() == 0


@pytest.mark.parametrize("method", ["post", "patch", "delete"])
def test_transcription_settings_other_methods(
    transcription_enabled, asr_service, method
):
    """Only GET and PUT are allowed."""
    room = RoomFactory()
    client, _ = owner_client(room)

    response = getattr(client, method)(
        settings_url(room), {"asr_model": "gigaam-multilingual-punct"}, format="json"
    )

    assert response.status_code == 405
    assert RoomTranscriptionSettings.objects.count() == 0
