"""Test the call history flag of the frontend configuration."""

import pytest
from rest_framework.test import APIClient

from meet.settings import Base

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("enabled", [True, False])
def test_api_config_exposes_calls(settings, enabled):
    """The frontend configuration tells whether the call history is enabled."""
    settings.CALLS_HISTORY_ENABLED = enabled

    response = APIClient().get("/api/v1.0/config/")

    assert response.status_code == 200
    assert response.json()["calls"] == {"enabled": enabled}


def test_calls_settings_defaults():
    """The call history is off by default; when on, transcripts are shared
    with participants and room administrators see the calls of their rooms.
    """
    assert Base.CALLS_HISTORY_ENABLED is False
    assert Base.CALLS_TRANSCRIPTS_VISIBLE_TO_PARTICIPANTS is True
    assert Base.CALLS_VISIBLE_TO_ROOM_ADMINS is True
