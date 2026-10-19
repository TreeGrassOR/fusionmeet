"""Service configuration, read from ``ASR_*`` environment variables."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from .registry import DEFAULT_MODEL, DEFAULT_MODELS, MODELS, RECOGNIZERS, vram_variable

# Variables of the single-model service, replaced by ASR_MODELS and the model
# registry: they are ignored, with a warning at startup.
REMOVED_VARIABLES = (
    "ASR_MODEL_ID",
    "ASR_MODEL_REVISION",
    "ASR_MODEL_VARIANT",
    "ASR_MODEL_VRAM_GB",
    "ASR_FALLBACK_REVISION",
    "ASR_FALLBACK_VARIANT",
    "ASR_FALLBACK_VRAM_GB",
)

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_LOG_LEVELS = {"critical", "error", "warning", "info", "debug", "trace"}


class ConfigError(ValueError):
    """Raised when the environment holds an invalid configuration."""


def default_cpu_threads() -> int:
    """Half of the CPUs this process may use, at least one."""
    try:
        cpus = len(os.sched_getaffinity(0))
    except AttributeError:  # not on Linux
        cpus = os.cpu_count() or 1
    return max(1, cpus // 2)


@dataclass(frozen=True)
class Settings:
    """Runtime settings of the ASR service."""

    api_key: str
    models: tuple[str, ...] = DEFAULT_MODELS
    default_model: str = DEFAULT_MODEL
    device: str = "cuda"
    max_vram_gb: float = 4.0
    # VRAM need per recogniser (GiB) that replaces the registry value.
    vram_gb: Mapping[str, float] = field(default_factory=dict)
    cpu_threads: int = field(default_factory=default_cpu_threads)
    fp16_encoder: bool = True
    max_batch_size: int = 8
    batch_wait_ms: float = 20.0
    max_batch_audio_seconds: float = 240.0
    max_audio_seconds: float = 30.0
    max_queue: int = 64
    log_level: str = "info"
    # Removed variables found in the environment (logged as ignored).
    ignored_variables: tuple[str, ...] = ()

    @property
    def device_type(self) -> str:
        """Return the device type without the index ("cuda:1" -> "cuda")."""
        return self.device.split(":", 1)[0]

    @property
    def device_index(self) -> int | None:
        """Return the device index ("cuda:1" -> 1), or None when none is given."""
        _, sep, index = self.device.partition(":")
        return int(index) if sep else None

    def model_vram_gb(self, recognizer_key: str) -> float:
        """GPU memory a recogniser needs: ASR_VRAM_GB_<ID> or the registry value."""
        backend = RECOGNIZERS[recognizer_key].gpu
        if backend is None:
            raise ValueError(f"{recognizer_key} has no GPU backend")
        return self.vram_gb.get(recognizer_key, backend.vram_gb)

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Settings:
        """Build the settings from environment variables.

        Raises:
            ConfigError: if a variable is missing or invalid.
        """
        env = os.environ if environ is None else environ
        reader = _EnvReader(env)

        api_key = reader.string("ASR_API_KEY", "")
        if not api_key:
            raise ConfigError(
                "ASR_API_KEY is required: set it to the shared secret that clients "
                "send as 'Authorization: Bearer <key>'"
            )

        vram_gb = {}
        for key, recognizer in RECOGNIZERS.items():
            if recognizer.gpu is not None:
                name = vram_variable(key)
                vram_gb[key] = reader.number(name, recognizer.gpu.vram_gb)

        settings = cls(
            api_key=api_key,
            models=reader.names("ASR_MODELS", cls.models),
            default_model=reader.string("ASR_DEFAULT_MODEL", cls.default_model).lower(),
            device=reader.string("ASR_DEVICE", cls.device).lower(),
            max_vram_gb=reader.number("ASR_MAX_VRAM_GB", cls.max_vram_gb),
            vram_gb=vram_gb,
            cpu_threads=reader.integer("ASR_CPU_THREADS", default_cpu_threads()),
            fp16_encoder=reader.boolean("ASR_FP16_ENCODER", cls.fp16_encoder),
            max_batch_size=reader.integer("ASR_MAX_BATCH_SIZE", cls.max_batch_size),
            batch_wait_ms=reader.number(
                "ASR_BATCH_WAIT_MS", cls.batch_wait_ms, allow_zero=True
            ),
            max_batch_audio_seconds=reader.number(
                "ASR_MAX_BATCH_AUDIO_SECONDS", cls.max_batch_audio_seconds
            ),
            max_audio_seconds=reader.number(
                "ASR_MAX_AUDIO_SECONDS", cls.max_audio_seconds
            ),
            max_queue=reader.integer("ASR_MAX_QUEUE", cls.max_queue),
            log_level=reader.string("ASR_LOG_LEVEL", cls.log_level).lower(),
            ignored_variables=tuple(
                name for name in REMOVED_VARIABLES if reader.is_set(name)
            ),
        )
        settings.validate()
        return settings

    def validate(self) -> None:
        """Check the cross-field constraints.

        Raises:
            ConfigError: if the settings are inconsistent.
        """
        _, sep, index = self.device.partition(":")
        valid_index = not sep or (index.isascii() and index.isdigit())
        if self.device_type not in {"cuda", "cpu"} or not valid_index:
            raise ConfigError(
                f"ASR_DEVICE must be 'cuda', 'cuda:<n>' or 'cpu', got {self.device!r}"
            )
        if self.log_level not in _LOG_LEVELS:
            raise ConfigError(f"ASR_LOG_LEVEL must be one of {sorted(_LOG_LEVELS)}")
        if not self.models:
            raise ConfigError("ASR_MODELS must name at least one model")
        unknown = [model for model in self.models if model not in MODELS]
        if unknown:
            raise ConfigError(
                f"ASR_MODELS: unknown model(s) {', '.join(unknown)}; "
                f"known models: {', '.join(MODELS)}"
            )
        if len(set(self.models)) != len(self.models):
            raise ConfigError("ASR_MODELS must not name a model twice")
        if self.default_model not in self.models:
            raise ConfigError(
                f"ASR_DEFAULT_MODEL={self.default_model} is not enabled in "
                f"ASR_MODELS ({', '.join(self.models)})"
            )
        if self.max_batch_audio_seconds < self.max_audio_seconds:
            raise ConfigError(
                "ASR_MAX_BATCH_AUDIO_SECONDS must be >= ASR_MAX_AUDIO_SECONDS, "
                "otherwise the longest allowed request could never be scheduled"
            )


class _EnvReader:
    """Typed accessors over an environment mapping."""

    def __init__(self, env: Mapping[str, str]):
        self._env = env

    def _raw(self, name: str) -> str | None:
        value = self._env.get(name)
        if value is None:
            return None
        value = value.strip()
        return value or None

    def is_set(self, name: str) -> bool:
        return self._raw(name) is not None

    def string(self, name: str, default: str) -> str:
        value = self._raw(name)
        # An empty variable means the default, as for the other types.
        return default if value is None else value

    def names(self, name: str, default: tuple[str, ...]) -> tuple[str, ...]:
        """Comma-separated list, lowercased; empty items are dropped."""
        value = self._raw(name)
        if value is None:
            return default
        return tuple(item.strip().lower() for item in value.split(",") if item.strip())

    def boolean(self, name: str, default: bool) -> bool:
        value = self._raw(name)
        if value is None:
            return default
        if value.lower() in _TRUE:
            return True
        if value.lower() in _FALSE:
            return False
        raise ConfigError(f"{name} must be a boolean (true/false), got {value!r}")

    def number(self, name: str, default: float, allow_zero: bool = False) -> float:
        value = self._raw(name)
        if value is None:
            return default
        try:
            number = float(value)
        except ValueError as exc:
            raise ConfigError(f"{name} must be a number, got {value!r}") from exc
        if not math.isfinite(number) or number < 0 or (number == 0 and not allow_zero):
            bound = ">= 0" if allow_zero else "> 0"
            raise ConfigError(f"{name} must be {bound}, got {value!r}")
        return number

    def integer(self, name: str, default: int) -> int:
        value = self._raw(name)
        if value is None:
            return default
        try:
            number = int(value)
        except ValueError as exc:
            raise ConfigError(f"{name} must be an integer, got {value!r}") from exc
        if number < 1:
            raise ConfigError(f"{name} must be >= 1, got {value!r}")
        return number
