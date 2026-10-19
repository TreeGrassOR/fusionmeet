"""Fixtures for tests in the transcripts application."""

# pylint: disable=redefined-outer-name,no-name-in-module

import copy
import json
import os
from dataclasses import dataclass, field
from unittest import mock

from django.core.cache import cache
from django.utils import timezone

import pytest
import responses
from livekit.api import AgentDispatch, ListRoomsResponse
from livekit.api import Room as LiveKitRoom

from transcripts.asr import CACHE_KEY as ASR_MODELS_CACHE_KEY
from transcripts.tests.utils import (
    AGENT_TOKEN,
    ASR_API_KEY,
    ASR_MODELS_PAYLOAD,
    ASR_MODELS_URL,
    ASR_URL,
)


@dataclass
class FakeLiveKit:
    """In-memory LiveKit room metadata and agent dispatches."""

    client: mock.AsyncMock
    live: bool = True
    metadata: dict = field(default_factory=dict)
    dispatches: list = field(default_factory=list)


@pytest.fixture
def livekit():
    """Patch the LiveKit API client with an in-memory fake.

    `list_rooms` returns the live room with its metadata (or no room when
    `live` is False), `update_room_metadata` stores the new metadata and
    `create_dispatch` records the dispatch request.
    """
    client = mock.AsyncMock()
    fake = FakeLiveKit(client=client)

    async def list_rooms(request):
        if not fake.live:
            return ListRoomsResponse(rooms=[])
        return ListRoomsResponse(
            rooms=[
                LiveKitRoom(name=request.names[0], metadata=json.dumps(fake.metadata))
            ]
        )

    async def update_room_metadata(request):
        fake.metadata = json.loads(request.metadata)
        return LiveKitRoom(name=request.room, metadata=request.metadata)

    async def create_dispatch(request):
        fake.dispatches.append(request)
        return AgentDispatch(id="AD_test", agent_name=request.agent_name)

    client.room.list_rooms.side_effect = list_rooms
    client.room.update_room_metadata.side_effect = update_room_metadata
    client.agent_dispatch.create_dispatch.side_effect = create_dispatch

    with mock.patch("core.utils.create_livekit_client", return_value=client):
        yield fake


@pytest.fixture
def frozen_now():
    """Freeze `django.utils.timezone.now` and return the frozen time.

    Unlike freezegun, the datetime class is not replaced: modules imported
    during the test (e.g. the URLconf on the first request) import normally.
    """
    now = timezone.now()
    with mock.patch("django.utils.timezone.now", return_value=now):
        yield now


@pytest.fixture
def transcription_enabled(settings):
    """Enable the transcription feature."""
    settings.TRANSCRIPTION_ENABLED = True
    settings.TRANSCRIPTION_AGENT_NAME = "multi-user-transcriber"
    settings.TRANSCRIPTION_S3_PREFIX = "transcriptions"
    return settings


@pytest.fixture
def agent_token(settings):
    """Configure the agent hook token."""
    settings.TRANSCRIPTION_AGENT_TOKEN = AGENT_TOKEN
    return AGENT_TOKEN


@pytest.fixture(autouse=True)
def clear_asr_models_cache(settings):
    """Start and end each test without a cached list of ASR models.

    The list is cached under a single key: each pytest-xdist worker gets its own
    key prefix, so that parallel tests sharing the Redis cache do not see the
    list cached by another one.
    """
    worker = os.environ.get("PYTEST_XDIST_WORKER", "main")
    settings.CACHES = {
        **settings.CACHES,
        "default": {**settings.CACHES["default"], "KEY_PREFIX": f"test-{worker}"},
    }
    cache.delete(ASR_MODELS_CACHE_KEY)
    yield
    cache.delete(ASR_MODELS_CACHE_KEY)


@pytest.fixture
def asr_service(settings):
    """Configure the ASR service and mock its `GET /v1/models` endpoint.

    Yields the `responses` mock: tests can replace the endpoint answer or count
    the calls. Any other HTTP request made with `requests` fails.
    """
    settings.TRANSCRIPTION_ASR_URL = ASR_URL
    settings.TRANSCRIPTION_ASR_API_KEY = ASR_API_KEY
    with responses.RequestsMock(assert_all_requests_are_fired=False) as mocked:
        mocked.get(ASR_MODELS_URL, json=copy.deepcopy(ASR_MODELS_PAYLOAD))
        yield mocked
