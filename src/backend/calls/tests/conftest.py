"""Fixtures for tests in the calls application."""

# pylint: disable=redefined-outer-name

import uuid

import pytest


@pytest.fixture(autouse=True)
def calls_enabled(settings):
    """Enable the call history, with the default visibility settings."""
    settings.CALLS_HISTORY_ENABLED = True
    settings.CALLS_TRANSCRIPTS_VISIBLE_TO_PARTICIPANTS = True
    settings.CALLS_VISIBLE_TO_ROOM_ADMINS = True
    return settings


@pytest.fixture(autouse=True)
def isolated_cache(settings):
    """Give each test its own cache keys, so that throttles start afresh.

    The Redis cache is shared by the tests (and by pytest-xdist workers).
    """
    settings.CACHES = {
        **settings.CACHES,
        "default": {
            **settings.CACHES["default"],
            "KEY_PREFIX": f"test-calls-{uuid.uuid4()}",
        },
    }
