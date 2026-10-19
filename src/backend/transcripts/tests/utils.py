"""Shared helpers for tests in the transcripts application."""

AGENT_TOKEN = "agent-token-for-tests"

ASR_URL = "http://asr.test:8000"
ASR_MODELS_URL = f"{ASR_URL}/v1/models"
ASR_API_KEY = "asr-key-for-tests"

# Answer of the ASR service `GET /v1/models` endpoint
ASR_MODELS_PAYLOAD = {
    "default": "gigaam-v3",
    "models": [
        {
            "id": "gigaam-v3",
            "label": "Русский — GigaAM v3",
            "description": "Лучшее качество для русской речи.",
            "languages": ["ru"],
            "punctuation": True,
            "device": "cuda",
            "status": "ready",
            "error": None,
        },
        {
            "id": "parakeet-v3",
            "label": "Русский и английский — Parakeet v3",
            "description": "Для встреч на английском или на двух языках.",
            "languages": ["ru", "en"],
            "punctuation": True,
            "device": "cpu",
            "status": "ready",
            "error": None,
        },
        {
            "id": "gigaam-multilingual",
            "label": "Прежняя модель — GigaAM Multilingual",
            "description": "Строчные буквы без знаков препинания.",
            "languages": ["ru", "en"],
            "punctuation": False,
            "device": None,
            "status": "failed",
            "error": "RuntimeError: out of memory",
        },
    ],
}
