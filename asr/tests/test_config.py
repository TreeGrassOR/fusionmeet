"""Tests of the environment configuration."""

import pytest

from asr_service.config import ConfigError, Settings, default_cpu_threads
from asr_service.main import create_app

ALL = ("gigaam-v3", "gigaam-multilingual", "gigaam-multilingual-punct")


def test_defaults():
    """Only the API key is required; the defaults match the documented contract."""
    settings = Settings.from_env({"ASR_API_KEY": "k"})
    assert settings.api_key == "k"
    assert settings.models == ALL
    assert settings.default_model == "gigaam-v3"
    assert settings.device == "cuda"
    assert settings.max_vram_gb == 4.0
    assert settings.model_vram_gb("gigaam-v3") == 1.5
    assert settings.model_vram_gb("gigaam-multilingual") == 3.0
    assert settings.cpu_threads == default_cpu_threads()
    assert settings.fp16_encoder is True
    assert settings.max_batch_size == 8
    assert settings.batch_wait_ms == 20.0
    assert settings.max_batch_audio_seconds == 240.0
    assert settings.max_audio_seconds == 30.0
    assert settings.max_queue == 64
    assert settings.ignored_variables == ()


def test_default_cpu_threads_is_half_the_cpus(monkeypatch):
    """ASR_CPU_THREADS defaults to max(1, CPUs // 2)."""
    monkeypatch.setattr("os.sched_getaffinity", lambda _pid: set(range(8)))
    assert default_cpu_threads() == 4
    monkeypatch.setattr("os.sched_getaffinity", lambda _pid: {0})
    assert default_cpu_threads() == 1


def test_overrides():
    """Every variable is parsed with its type."""
    settings = Settings.from_env(
        {
            "ASR_API_KEY": "k",
            "ASR_MODELS": " GigaAM-Multilingual , gigaam-v3,, ",
            "ASR_DEFAULT_MODEL": "gigaam-multilingual",
            "ASR_MAX_VRAM_GB": "6.5",
            "ASR_VRAM_GB_GIGAAM_V3": "2",
            "ASR_VRAM_GB_GIGAAM_MULTILINGUAL": "2.75",
            "ASR_CPU_THREADS": "3",
            "ASR_DEVICE": "CPU",
            "ASR_FP16_ENCODER": "false",
            "ASR_MAX_BATCH_SIZE": "4",
            "ASR_BATCH_WAIT_MS": "0",
            "ASR_MAX_AUDIO_SECONDS": "20",
            "ASR_MAX_QUEUE": "10",
            "ASR_LOG_LEVEL": "DEBUG",
        }
    )
    assert settings.models == ("gigaam-multilingual", "gigaam-v3")
    assert settings.default_model == "gigaam-multilingual"
    assert settings.max_vram_gb == 6.5
    assert settings.model_vram_gb("gigaam-v3") == 2.0
    assert settings.model_vram_gb("gigaam-multilingual") == 2.75
    assert settings.cpu_threads == 3
    assert settings.device == "cpu"
    assert settings.fp16_encoder is False
    assert settings.max_batch_size == 4
    assert settings.batch_wait_ms == 0
    assert settings.max_audio_seconds == 20
    assert settings.max_queue == 10
    assert settings.log_level == "debug"


@pytest.mark.parametrize("env", [{}, {"ASR_API_KEY": ""}, {"ASR_API_KEY": "   "}])
def test_api_key_is_required(env):
    """The service refuses to start without a key."""
    with pytest.raises(ConfigError, match="ASR_API_KEY is required"):
        Settings.from_env(env)


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("ASR_MAX_VRAM_GB", "lots", "must be a number"),
        ("ASR_MAX_VRAM_GB", "0", "must be > 0"),
        ("ASR_MAX_VRAM_GB", "nan", "must be > 0"),
        ("ASR_VRAM_GB_GIGAAM_V3", "-1", "ASR_VRAM_GB_GIGAAM_V3 must be > 0"),
        ("ASR_CPU_THREADS", "0", "ASR_CPU_THREADS must be >= 1"),
        ("ASR_BATCH_WAIT_MS", "-1", "must be >= 0"),
        ("ASR_MAX_BATCH_SIZE", "2.5", "must be an integer"),
        ("ASR_MAX_QUEUE", "0", "must be >= 1"),
        ("ASR_FP16_ENCODER", "maybe", "must be a boolean"),
        ("ASR_DEVICE", "tpu", "ASR_DEVICE must be"),
        ("ASR_LOG_LEVEL", "loud", "ASR_LOG_LEVEL must be"),
        ("ASR_MAX_BATCH_AUDIO_SECONDS", "10", "ASR_MAX_BATCH_AUDIO_SECONDS must be >="),
        ("ASR_MODELS", "whisper", "unknown model\\(s\\) whisper; known models: gigaam"),
        ("ASR_MODELS", "gigaam-v3,gigaam-v3", "must not name a model twice"),
        ("ASR_MODELS", " , ", "ASR_MODELS must name at least one model"),
        ("ASR_DEFAULT_MODEL", "nope", "ASR_DEFAULT_MODEL=nope is not enabled"),
    ],
)
def test_invalid_values(name, value, message):
    """Invalid values are reported with the variable name."""
    with pytest.raises(ConfigError, match=message):
        Settings.from_env({"ASR_API_KEY": "k", name: value})


def test_default_model_must_be_enabled():
    """ASR_DEFAULT_MODEL has to be one of ASR_MODELS."""
    with pytest.raises(ConfigError, match="is not enabled in ASR_MODELS"):
        Settings.from_env({"ASR_API_KEY": "k", "ASR_MODELS": "gigaam-multilingual"})


def test_removed_variables_are_reported_as_ignored():
    """The single-model variables are ignored; startup logs which ones were set."""
    settings = Settings.from_env(
        {
            "ASR_API_KEY": "k",
            "ASR_MODEL_REVISION": "abc",
            "ASR_FALLBACK_REVISION": "",
            "ASR_MODEL_VRAM_GB": "3",
        }
    )
    assert settings.ignored_variables == ("ASR_MODEL_REVISION", "ASR_MODEL_VRAM_GB")
    assert settings.models == ALL


def test_cuda_index_is_accepted():
    """A specific GPU can be selected."""
    settings = Settings.from_env({"ASR_API_KEY": "k", "ASR_DEVICE": "cuda:1"})
    assert settings.device_type == "cuda"
    assert settings.device_index == 1
    assert Settings.from_env({"ASR_API_KEY": "k"}).device_index is None


def test_factory_exits_without_api_key(monkeypatch):
    """The uvicorn factory exits with status 1 when the key is missing."""
    monkeypatch.delenv("ASR_API_KEY", raising=False)
    with pytest.raises(SystemExit) as excinfo:
        create_app()
    assert excinfo.value.code == 1


@pytest.mark.parametrize("device", ["cuda:", "cuda:x", "cuda:-1", "cuda:1:2"])
def test_invalid_device_index(device):
    """A device index must be a non-negative integer."""
    with pytest.raises(ConfigError, match="ASR_DEVICE must be"):
        Settings.from_env({"ASR_API_KEY": "k", "ASR_DEVICE": device})


def test_empty_values_mean_the_default():
    """Empty variables (e.g. an unset ${VAR} in compose) keep the defaults."""
    names = [
        "ASR_MODELS",
        "ASR_DEFAULT_MODEL",
        "ASR_DEVICE",
        "ASR_LOG_LEVEL",
        "ASR_MAX_VRAM_GB",
        "ASR_VRAM_GB_GIGAAM_V3",
        "ASR_CPU_THREADS",
        "ASR_FP16_ENCODER",
        "ASR_MAX_BATCH_SIZE",
        "ASR_MODEL_REVISION",
    ]
    settings = Settings.from_env({"ASR_API_KEY": "k", **dict.fromkeys(names, "")})
    assert settings == Settings(
        api_key="k", vram_gb={"gigaam-v3": 1.5, "gigaam-multilingual": 3.0}
    )
