"""Service configuration, read from ``ASR_*`` environment variables."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass

# Branch "large_ctc" (585M parameters) of ai-sage/GigaAM-Multilingual.
DEFAULT_MODEL_REVISION = "3905cd51c3ed4e88c8edf33f3302969ba480a327"
# Branch "ctc" (= "main", 221M parameters) of ai-sage/GigaAM-Multilingual.
DEFAULT_FALLBACK_REVISION = "2f8a57144e6ec3adfd32fe0484d9ea9913305bc8"

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}
_LOG_LEVELS = {"critical", "error", "warning", "info", "debug", "trace"}


class ConfigError(ValueError):
    """Raised when the environment holds an invalid configuration."""


@dataclass(frozen=True)
class Settings:
    """Runtime settings of the ASR service."""

    api_key: str
    model_id: str = "ai-sage/GigaAM-Multilingual"
    model_revision: str = DEFAULT_MODEL_REVISION
    model_variant: str = "large_ctc"
    model_vram_gb: float = 3.0
    fallback_revision: str | None = DEFAULT_FALLBACK_REVISION
    fallback_variant: str = "ctc"
    fallback_vram_gb: float = 1.5
    max_vram_gb: float = 4.0
    device: str = "cuda"
    fp16_encoder: bool = True
    max_batch_size: int = 8
    batch_wait_ms: float = 20.0
    max_batch_audio_seconds: float = 240.0
    max_audio_seconds: float = 30.0
    max_queue: int = 64
    log_level: str = "info"

    @property
    def device_type(self) -> str:
        """Return the device type without the index ("cuda:1" -> "cuda")."""
        return self.device.split(":", 1)[0]

    @property
    def device_index(self) -> int | None:
        """Return the device index ("cuda:1" -> 1), or None when none is given."""
        _, sep, index = self.device.partition(":")
        return int(index) if sep else None

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

        settings = cls(
            api_key=api_key,
            model_id=reader.string("ASR_MODEL_ID", cls.model_id),
            model_revision=reader.string("ASR_MODEL_REVISION", cls.model_revision),
            model_variant=reader.string("ASR_MODEL_VARIANT", cls.model_variant),
            model_vram_gb=reader.number("ASR_MODEL_VRAM_GB", cls.model_vram_gb),
            fallback_revision=reader.string(
                "ASR_FALLBACK_REVISION", DEFAULT_FALLBACK_REVISION, empty_disables=True
            )
            or None,
            fallback_variant=reader.string(
                "ASR_FALLBACK_VARIANT", cls.fallback_variant
            ),
            fallback_vram_gb=reader.number(
                "ASR_FALLBACK_VRAM_GB", cls.fallback_vram_gb
            ),
            max_vram_gb=reader.number("ASR_MAX_VRAM_GB", cls.max_vram_gb),
            device=reader.string("ASR_DEVICE", cls.device).lower(),
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
        if not self.model_id or not self.model_revision:
            raise ConfigError("ASR_MODEL_ID and ASR_MODEL_REVISION must not be empty")
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

    def string(self, name: str, default: str, empty_disables: bool = False) -> str:
        value = self._raw(name)
        if value is None:
            # Like the other types, an empty variable means the default, except for
            # optional values such as ASR_FALLBACK_REVISION where it means "disabled".
            return "" if empty_disables and name in self._env else default
        return value

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
