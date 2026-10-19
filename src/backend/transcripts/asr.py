"""Recognition models of the ASR service, and the model chosen in each room.

The list comes from `GET <TRANSCRIPTION_ASR_URL>/v1/models`. It is cached for
TRANSCRIPTION_MODELS_CACHE_SECONDS in the shared cache, so the settings dialog
and the agent polling the room settings do not call the ASR service each time.
"""

from logging import getLogger

from django.conf import settings
from django.core.cache import cache

import requests

from .models import RoomTranscriptionSettings

logger = getLogger(__name__)

CACHE_KEY = "transcription:asr-models"
# A list with models still loading is read again sooner, so that they become
# selectable shortly after they are ready.
LOADING_CACHE_SECONDS = 5
REQUEST_TIMEOUT_SECONDS = 5

MODEL_STATUS_LOADING = "loading"
MODEL_STATUS_FAILED = "failed"


class AsrModelsUnavailableError(Exception):
    """The list of recognition models cannot be read from the ASR service."""


def is_valid_models_payload(payload) -> bool:
    """Whether a `/v1/models` payload has the shape the backend relies on."""
    if not isinstance(payload, dict) or not isinstance(payload.get("models"), list):
        return False

    return all(
        isinstance(model, dict) and isinstance(model.get("id"), str) and model["id"]
        for model in payload["models"]
    )


def fetch_models() -> dict:
    """Read the list of recognition models from the ASR service.

    Raises:
        AsrModelsUnavailableError: the service cannot be reached, refuses the
            request or answers with an invalid list.
    """
    url = f"{settings.TRANSCRIPTION_ASR_URL.rstrip('/')}/v1/models"
    headers = {}
    if settings.TRANSCRIPTION_ASR_API_KEY:
        headers["Authorization"] = f"Bearer {settings.TRANSCRIPTION_ASR_API_KEY}"

    try:
        response = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError) as e:
        logger.warning("Could not list the ASR models from %s: %s", url, e)
        raise AsrModelsUnavailableError("The ASR service cannot be reached") from e

    if not is_valid_models_payload(payload):
        logger.warning("The ASR service at %s returned an invalid list of models", url)
        raise AsrModelsUnavailableError("Invalid list of ASR models")

    return payload


def get_models() -> dict:
    """Return the `/v1/models` payload of the ASR service, cached.

    Raises:
        AsrModelsUnavailableError: TRANSCRIPTION_ASR_URL is not set, or the list
            cannot be read.
    """
    if not settings.TRANSCRIPTION_ASR_URL:
        raise AsrModelsUnavailableError("The ASR service is not configured")

    payload = cache.get(CACHE_KEY)
    if payload is not None:
        return payload

    payload = fetch_models()
    timeout = settings.TRANSCRIPTION_MODELS_CACHE_SECONDS
    if any(model.get("status") == MODEL_STATUS_LOADING for model in payload["models"]):
        timeout = min(timeout, LOADING_CACHE_SECONDS)
    cache.set(CACHE_KEY, payload, timeout)
    return payload


def find_model(payload, model_id):
    """Return the model of a `/v1/models` payload with this id, or None."""
    return next((model for model in payload["models"] if model["id"] == model_id), None)


def is_selectable(model) -> bool:
    """Whether a room can use a model: it exists and did not fail to load.

    A model still loading can be chosen: it is ready within minutes.
    """
    return model is not None and model.get("status") != MODEL_STATUS_FAILED


def get_room_asr_model(room_id):
    """Model the transcriber agent must request in a room, None for the default.

    A model the ASR service does not offer anymore (removed from ASR_MODELS, or
    failed to load) is replaced by the default one, so that the phrases are
    still recognized. When the list cannot be read, the room choice is kept.
    """
    model_id = (
        RoomTranscriptionSettings.objects.filter(room_id=room_id)
        .values_list("asr_model", flat=True)
        .first()
    )
    if not model_id:
        return None

    try:
        payload = get_models()
    except AsrModelsUnavailableError:
        return model_id

    if not is_selectable(find_model(payload, model_id)):
        logger.info(
            "Model %s chosen in room %s is not available, using the default model",
            model_id,
            room_id,
        )
        return None

    return model_id
