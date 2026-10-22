"""Test the call history flag of the frontend configuration."""

import pytest
from rest_framework.test import APIClient

from meet.settings import Base

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("enabled", [True, False])
@pytest.mark.parametrize("visible_to_room_admins", [True, False])
def test_api_config_exposes_calls(settings, enabled, visible_to_room_admins):
    """The frontend configuration tells whether the call history is enabled,
    and whether room administrators see it (the chat says who reads it).
    """
    settings.CALLS_HISTORY_ENABLED = enabled
    settings.CALLS_VISIBLE_TO_ROOM_ADMINS = visible_to_room_admins

    response = APIClient().get("/api/v1.0/config/")

    assert response.status_code == 200
    assert response.json()["calls"] == {
        "enabled": enabled,
        "visible_to_room_admins": visible_to_room_admins,
    }


def test_calls_settings_defaults():
    """The call history is off by default; when on, transcripts are shared
    with participants and room administrators see the calls of their rooms.
    """
    assert Base.CALLS_HISTORY_ENABLED is False
    assert Base.CALLS_TRANSCRIPTS_VISIBLE_TO_PARTICIPANTS is True
    assert Base.CALLS_VISIBLE_TO_ROOM_ADMINS is True
