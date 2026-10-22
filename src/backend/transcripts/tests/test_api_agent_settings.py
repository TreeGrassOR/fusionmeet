"""Test the endpoint telling the agent which recognition model to use."""

# pylint: disable=redefined-outer-name,unused-argument

import copy

import pytest
import requests
from rest_framework.test import APIClient

from core.factories import RoomFactory, UserFactory
from core.models import RoleChoices

from transcripts.models import RoomTranscriptionSettings
from transcripts.tests.utils import AGENT_TOKEN, ASR_MODELS_PAYLOAD, ASR_MODELS_URL

pytestmark = pytest.mark.django_db

AGENT_SETTINGS_URL = "/api/v1.0/transcripts/agent-settings/"


def get_agent_settings(room_id, token=AGENT_TOKEN):
    """Get the settings of a room as the agent does, with a Bearer token."""
    headers = {"HTTP_AUTHORIZATION": f"Bearer {token}"} if token else {}
    params = {} if room_id is None else {"room": room_id}
    return APIClient().get(AGENT_SETTINGS_URL, params, **headers)


def room_with_model(asr_model):
    """Create a room whose recognition model is set."""
    room = RoomFactory()
    RoomTranscriptionSettings.objects.create(room=room, asr_model=asr_model)
    return room


# Authentication


@pytest.mark.parametrize("token_setting", [None, ""])
def test_agent_settings_disabled_without_token(settings, asr_service, token_setting):
    """The endpoint does not exist when no agent token is configured."""
    settings.TRANSCRIPTION_AGENT_TOKEN = token_setting
    room = room_with_model("gigaam-multilingual-punct")

    response = get_agent_settings(str(room.id), token="anything")

    assert response.status_code == 404


@pytest.mark.parametrize(
    "authorization",
    [None, "Bearer wrong-token", f"Token {AGENT_TOKEN}", "Bearer", AGENT_TOKEN],
)
def test_agent_settings_wrong_token(agent_token, asr_service, authorization):
    """Requests without the right Bearer token are rejected."""
    room = room_with_model("gigaam-multilingual-punct")
    headers = {"HTTP_AUTHORIZATION": authorization} if authorization else {}

    response = APIClient().get(AGENT_SETTINGS_URL, {"room": str(room.id)}, **headers)

    assert response.status_code == 401
    assert "asr_model" not in response.json()


def test_agent_settings_user_session_rejected(agent_token, asr_service):
    """A logged-in user cannot call the agent endpoint."""
    room = room_with_model("gigaam-multilingual-punct")
    user = UserFactory()
    room.accesses.create(user=user, role=RoleChoices.OWNER)
    client = APIClient()
    client.force_login(user)

    response = client.get(AGENT_SETTINGS_URL, {"room": str(room.id)})

    assert response.status_code == 401


# Room parameter


@pytest.mark.parametrize("room_id", [None, "", "not-a-uuid", "1234"])
def test_agent_settings_invalid_room(agent_token, asr_service, room_id):
    """The room parameter must be a room id."""
    response = get_agent_settings(room_id)

    assert response.status_code == 400
    assert response.json() == {"room": "Must be the id of a room."}


def test_agent_settings_unknown_room(agent_token, asr_service):
    """An unknown room uses the default model."""
    response = get_agent_settings("7b8e3a3c-5d2a-4b1a-9c3e-0d6f1e2a3b4c")

    assert response.status_code == 200
    assert response.json() == {"asr_model": None}
    assert len(asr_service.calls) == 0


# Model of the room


def test_agent_settings_default(agent_token, asr_service):
    """A room without settings uses the default model, without calling the ASR."""
    response = get_agent_settings(str(RoomFactory().id))

    assert response.status_code == 200
    assert response.json() == {"asr_model": None}
    assert len(asr_service.calls) == 0


def test_agent_settings_reset(agent_token, asr_service):
    """A room reset to the default model gives null."""
    room = room_with_model("")

    response = get_agent_settings(str(room.id))

    assert response.json() == {"asr_model": None}
    assert len(asr_service.calls) == 0


def test_agent_settings_chosen(agent_token, asr_service):
    """The model chosen for the room is returned while the ASR service offers it."""
    room = room_with_model("gigaam-multilingual-punct")
    room_with_model("gigaam-v3")

    response = get_agent_settings(str(room.id))

    assert response.status_code == 200
    assert response.json() == {"asr_model": "gigaam-multilingual-punct"}

    # The list of models is cached between the polls of the agent
    assert get_agent_settings(str(room.id)).json() == {
        "asr_model": "gigaam-multilingual-punct"
    }
    assert len(asr_service.calls) == 1


def test_agent_settings_room_id_formats(agent_token, asr_service):
    """The LiveKit room name is the room id, in any UUID spelling."""
    room = room_with_model("gigaam-multilingual-punct")

    response = get_agent_settings(str(room.id).upper())

    assert response.json() == {"asr_model": "gigaam-multilingual-punct"}


def test_agent_settings_loading_model(agent_token, asr_service):
    """A model still loading is kept: the agent retries until it is ready."""
    payload = copy.deepcopy(ASR_MODELS_PAYLOAD)
    payload["models"][1]["status"] = "loading"
    asr_service.replace("GET", ASR_MODELS_URL, json=payload)
    room = room_with_model("gigaam-multilingual-punct")

    response = get_agent_settings(str(room.id))

    assert response.json() == {"asr_model": "gigaam-multilingual-punct"}


@pytest.mark.parametrize("asr_model", ["gigaam-multilingual", "removed-model"])
def test_agent_settings_model_not_offered(agent_token, asr_service, asr_model):
    """A model that failed or is no longer offered falls back to the default."""
    room = room_with_model(asr_model)

    response = get_agent_settings(str(room.id))

    assert response.status_code == 200
    assert response.json() == {"asr_model": None}
    # The choice of the room is kept for when the model is back
    assert RoomTranscriptionSettings.objects.get(room=room).asr_model == asr_model


@pytest.mark.parametrize(
    "answer",
    [
        {"body": requests.ConnectionError("refused")},
        {"status": 503, "json": {"status": "loading"}},
    ],
)
def test_agent_settings_asr_unavailable(agent_token, asr_service, answer):
    """When the list cannot be read, the model chosen for the room is kept."""
    asr_service.replace("GET", ASR_MODELS_URL, **answer)
    room = room_with_model("gigaam-multilingual-punct")

    response = get_agent_settings(str(room.id))

    assert response.status_code == 200
    assert response.json() == {"asr_model": "gigaam-multilingual-punct"}


def test_agent_settings_asr_not_configured(agent_token, settings):
    """Without TRANSCRIPTION_ASR_URL, the model chosen for the room is kept."""
    settings.TRANSCRIPTION_ASR_URL = None
    room = room_with_model("gigaam-multilingual-punct")

    response = get_agent_settings(str(room.id))

    assert response.json() == {"asr_model": "gigaam-multilingual-punct"}


def test_agent_settings_follows_room_changes(
    transcription_enabled, agent_token, asr_service
):
    """A model chosen by the room owner is the one the agent gets next."""
    room = RoomFactory()
    owner = UserFactory()
    room.accesses.create(user=owner, role=RoleChoices.OWNER)
    client = APIClient()
    client.force_login(owner)
    url = f"/api/v1.0/rooms/{room.id}/transcription-settings/"

    assert get_agent_settings(str(room.id)).json() == {"asr_model": None}

    response = client.put(
        url, {"asr_model": "gigaam-multilingual-punct"}, format="json"
    )
    assert response.status_code == 200
    assert get_agent_settings(str(room.id)).json() == {
        "asr_model": "gigaam-multilingual-punct"
    }

    response = client.put(url, {"asr_model": None}, format="json")
    assert response.status_code == 200
    assert get_agent_settings(str(room.id)).json() == {"asr_model": None}
