"""Test the endpoint listing the recognition models of the ASR service."""

# pylint: disable=redefined-outer-name,unused-argument

import copy

from django.core.cache import cache

import pytest
import requests
from rest_framework.test import APIClient

from core.factories import UserFactory

from transcripts.asr import CACHE_KEY
from transcripts.tests.utils import ASR_API_KEY, ASR_MODELS_PAYLOAD, ASR_MODELS_URL

pytestmark = pytest.mark.django_db

MODELS_URL = "/api/v1.0/transcription/models/"


def get_models(user=None):
    """Get the list of models as a logged-in user (a new one by default)."""
    client = APIClient()
    client.force_login(user or UserFactory())
    return client.get(MODELS_URL)


def test_transcription_models_anonymous(transcription_enabled, asr_service):
    """Anonymous users cannot list the models."""
    response = APIClient().get(MODELS_URL)

    assert response.status_code == 401
    assert len(asr_service.calls) == 0


def test_transcription_models_disabled(settings, asr_service):
    """The endpoint does not exist when the transcription is disabled."""
    settings.TRANSCRIPTION_ENABLED = False

    response = get_models()

    assert response.status_code == 404
    assert len(asr_service.calls) == 0


def test_transcription_models_success(transcription_enabled, asr_service):
    """The payload of the ASR service is returned as is, with the API key."""
    response = get_models()

    assert response.status_code == 200
    assert response.json() == ASR_MODELS_PAYLOAD
    assert len(asr_service.calls) == 1
    request = asr_service.calls[0].request
    assert request.url == ASR_MODELS_URL
    assert request.headers["Authorization"] == f"Bearer {ASR_API_KEY}"


def test_transcription_models_url_with_trailing_slash(
    transcription_enabled, asr_service
):
    """A trailing slash in TRANSCRIPTION_ASR_URL is ignored."""
    transcription_enabled.TRANSCRIPTION_ASR_URL = "http://asr.test:8000/"

    response = get_models()

    assert response.status_code == 200
    assert asr_service.calls[0].request.url == ASR_MODELS_URL


@pytest.mark.parametrize("api_key", [None, ""])
def test_transcription_models_without_api_key(
    transcription_enabled, asr_service, api_key
):
    """No Authorization header is sent when no API key is configured."""
    transcription_enabled.TRANSCRIPTION_ASR_API_KEY = api_key

    response = get_models()

    assert response.status_code == 200
    assert "Authorization" not in asr_service.calls[0].request.headers


def test_transcription_models_cached(transcription_enabled, asr_service):
    """The list is cached for TRANSCRIPTION_MODELS_CACHE_SECONDS."""
    transcription_enabled.TRANSCRIPTION_MODELS_CACHE_SECONDS = 60

    assert get_models().json() == ASR_MODELS_PAYLOAD
    assert get_models().json() == ASR_MODELS_PAYLOAD

    assert len(asr_service.calls) == 1
    assert 55 <= cache.ttl(CACHE_KEY) <= 60

    # Once the cache expires, the list is read again
    cache.delete(CACHE_KEY)
    assert get_models().status_code == 200
    assert len(asr_service.calls) == 2


def test_transcription_models_cache_disabled(transcription_enabled, asr_service):
    """TRANSCRIPTION_MODELS_CACHE_SECONDS=0 reads the list on each request."""
    transcription_enabled.TRANSCRIPTION_MODELS_CACHE_SECONDS = 0

    assert get_models().status_code == 200
    assert get_models().status_code == 200

    assert len(asr_service.calls) == 2


def test_transcription_models_loading_cached_shortly(
    transcription_enabled, asr_service
):
    """A list with models still loading is cached for a few seconds only."""
    transcription_enabled.TRANSCRIPTION_MODELS_CACHE_SECONDS = 60
    payload = copy.deepcopy(ASR_MODELS_PAYLOAD)
    payload["models"][1]["status"] = "loading"
    asr_service.replace("GET", ASR_MODELS_URL, json=payload)

    response = get_models()

    assert response.status_code == 200
    assert response.json()["models"][1]["status"] == "loading"
    assert 0 < cache.ttl(CACHE_KEY) <= 5


@pytest.mark.parametrize("asr_url", [None, ""])
def test_transcription_models_not_configured(
    transcription_enabled, asr_service, asr_url
):
    """Without TRANSCRIPTION_ASR_URL, the list is unavailable."""
    transcription_enabled.TRANSCRIPTION_ASR_URL = asr_url

    response = get_models()

    assert response.status_code == 503
    assert response.json() == {"detail": "The recognition models are unavailable."}
    assert len(asr_service.calls) == 0


@pytest.mark.parametrize(
    "answer",
    [
        {"body": requests.ConnectionError("refused")},
        {"body": requests.Timeout("timed out")},
        {"status": 401, "json": {"detail": "Invalid API key"}},
        {"status": 500, "body": "Internal Server Error"},
        {"status": 503, "json": {"status": "loading"}},
        {"status": 200, "body": "not json"},
        {"status": 200, "json": ["gigaam-v3"]},
        {"status": 200, "json": {"default": "gigaam-v3"}},
        {"status": 200, "json": {"default": "gigaam-v3", "models": ["gigaam-v3"]}},
        {"status": 200, "json": {"default": "gigaam-v3", "models": [{"id": ""}]}},
        {"status": 200, "json": {"default": "gigaam-v3", "models": [{"id": 1}]}},
    ],
)
def test_transcription_models_unavailable(transcription_enabled, asr_service, answer):
    """Errors and invalid answers of the ASR service give a 503, not cached."""
    asr_service.replace("GET", ASR_MODELS_URL, **answer)

    response = get_models()

    assert response.status_code == 503
    assert response.json() == {"detail": "The recognition models are unavailable."}
    assert cache.get(CACHE_KEY) is None

    # The next request reads the list again
    asr_service.replace("GET", ASR_MODELS_URL, json=ASR_MODELS_PAYLOAD)
    assert get_models().status_code == 200
    assert len(asr_service.calls) == 2
