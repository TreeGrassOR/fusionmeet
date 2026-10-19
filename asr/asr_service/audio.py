"""Decoding of request bodies (RIFF WAV or raw PCM) into float32 waveforms."""

from __future__ import annotations

import struct

import numpy as np

SAMPLE_RATE = 16_000

WAV_CONTENT_TYPES = frozenset(
    {"audio/wav", "audio/x-wav", "audio/wave", "audio/vnd.wave"}
)
PCM_CONTENT_TYPES = frozenset({"audio/pcm", "audio/l16", "application/octet-stream"})

_WAVE_FORMAT_PCM = 0x0001
_WAVE_FORMAT_IEEE_FLOAT = 0x0003
_WAVE_FORMAT_EXTENSIBLE = 0xFFFE
_FORMAT_NAMES = {
    0x0002: "MS ADPCM",
    0x0006: "A-law",
    0x0007: "mu-law",
    0x0011: "IMA ADPCM",
    0x0031: "GSM 6.10",
    0x0055: "MP3",
}
_EXPECTED = "expected 16 kHz mono, 16-bit PCM or 32-bit float"


class AudioError(Exception):
    """Base class for audio decoding errors; ``status_code`` is the HTTP status."""

    status_code = 400


class InvalidAudioError(AudioError):
    """The body is not decodable audio (garbage, truncated header...)."""

    status_code = 400


class UnsupportedAudioError(AudioError):
    """The body is valid audio in a format the service does not accept."""

    status_code = 422


def decode_audio(body: bytes, content_type: str | None) -> np.ndarray:
    """Decode a request body into a mono float32 waveform in [-1, 1] at 16 kHz.

    Args:
        body: the raw request body.
        content_type: the request Content-Type header, parameters included.

    Raises:
        InvalidAudioError: if the body cannot be decoded.
        UnsupportedAudioError: if the sample rate, channel count, codec or media type
            is not supported.
    """
    media_type, params = _parse_content_type(content_type)
    if not body:
        raise InvalidAudioError("Empty request body")
    if media_type is None:
        # No Content-Type: sniff the RIFF magic, otherwise treat the body as raw PCM.
        media_type = "audio/wav" if body[:4] == b"RIFF" else "audio/pcm"
    if media_type in WAV_CONTENT_TYPES:
        return decode_wav(body)
    if media_type in PCM_CONTENT_TYPES:
        return decode_pcm16(body, params)
    supported = ", ".join(sorted(WAV_CONTENT_TYPES | PCM_CONTENT_TYPES))
    raise UnsupportedAudioError(
        f"Unsupported Content-Type {media_type!r}; send one of: {supported}"
    )


def decode_pcm16(body: bytes, params: dict[str, str] | None = None) -> np.ndarray:
    """Decode raw signed 16-bit little-endian mono PCM at 16 kHz.

    ``rate`` and ``channels`` Content-Type parameters, when present, must match.
    """
    params = params or {}
    rate = params.get("rate")
    if rate is not None and rate != str(SAMPLE_RATE):
        raise UnsupportedAudioError(f"Unsupported sample rate {rate} Hz; {_EXPECTED}")
    channels = params.get("channels")
    if channels is not None and channels != "1":
        raise UnsupportedAudioError(
            f"Unsupported channel count {channels}; {_EXPECTED}"
        )
    if len(body) % 2:
        raise InvalidAudioError(
            "Raw PCM body length is not a whole number of 16-bit samples"
        )
    return _int16_to_float(body)


def decode_wav(body: bytes) -> np.ndarray:
    """Decode a RIFF/WAVE file (PCM 16-bit or IEEE float 32-bit, mono, 16 kHz)."""
    fmt, data = _read_chunks(body)
    audio_format, channels, sample_rate, block_align, bits = fmt
    _check_format(audio_format, channels, sample_rate, bits)
    if block_align != bits // 8:
        raise InvalidAudioError(f"Inconsistent WAV header: block align {block_align}")

    # Drop a trailing partial sample, if any (truncated upload).
    data = data[: len(data) - len(data) % block_align]
    if audio_format == _WAVE_FORMAT_PCM:
        return _int16_to_float(data)
    samples = np.frombuffer(data, dtype="<f4").astype(np.float32)
    if not np.isfinite(samples).all():
        raise InvalidAudioError("WAV float samples contain NaN or infinity")
    return np.clip(samples, -1.0, 1.0)


def _read_chunks(body: bytes) -> tuple[tuple[int, int, int, int, int], bytes]:
    """Return the parsed 'fmt ' chunk and the bytes of the 'data' chunk."""
    if len(body) < 12 or body[:4] != b"RIFF" or body[8:12] != b"WAVE":
        raise InvalidAudioError("Body is not a RIFF/WAVE file")
    fmt = None
    offset = 12
    while offset + 8 <= len(body):
        chunk_id = body[offset : offset + 4]
        (chunk_size,) = struct.unpack_from("<I", body, offset + 4)
        start = offset + 8
        if chunk_id == b"fmt ":
            fmt = _parse_fmt(body[start : start + chunk_size])
        elif chunk_id == b"data":
            if fmt is None:
                raise InvalidAudioError("WAV 'data' chunk found before 'fmt '")
            # Streaming writers leave the size at 0 or 0xFFFFFFFF, and truncated
            # uploads happen: use what is actually there.
            end = len(body) if chunk_size in (0, 0xFFFFFFFF) else start + chunk_size
            return fmt, body[start : min(end, len(body))]
        offset = start + chunk_size + (chunk_size & 1)
    if fmt is None:
        raise InvalidAudioError("WAV file has no 'fmt ' chunk")
    raise InvalidAudioError("WAV file has no 'data' chunk")


def _check_format(
    audio_format: int, channels: int, sample_rate: int, bits: int
) -> None:
    """Refuse (422) every WAV layout other than 16 kHz mono PCM16 / float32."""
    if audio_format not in (_WAVE_FORMAT_PCM, _WAVE_FORMAT_IEEE_FLOAT):
        name = _FORMAT_NAMES.get(audio_format, f"format tag 0x{audio_format:04x}")
        raise UnsupportedAudioError(f"Unsupported WAV codec {name}; {_EXPECTED}")
    expected_bits = 16 if audio_format == _WAVE_FORMAT_PCM else 32
    if bits != expected_bits:
        kind = "PCM" if audio_format == _WAVE_FORMAT_PCM else "float"
        raise UnsupportedAudioError(
            f"Unsupported WAV sample format: {bits}-bit {kind}; {_EXPECTED}"
        )
    if channels != 1:
        raise UnsupportedAudioError(
            f"Unsupported channel count {channels}; {_EXPECTED}"
        )
    if sample_rate != SAMPLE_RATE:
        raise UnsupportedAudioError(
            f"Unsupported sample rate {sample_rate} Hz; {_EXPECTED}"
        )


def _parse_fmt(chunk: bytes) -> tuple[int, int, int, int, int]:
    """Return (format tag, channels, sample rate, block align, bits) of a fmt chunk."""
    if len(chunk) < 16:
        raise InvalidAudioError("WAV 'fmt ' chunk is truncated")
    audio_format, channels, sample_rate, _, block_align, bits = struct.unpack_from(
        "<HHIIHH", chunk
    )
    if audio_format == _WAVE_FORMAT_EXTENSIBLE:
        # WAVEFORMATEXTENSIBLE: the real format tag is the first 2 bytes of the
        # sub-format GUID, at offset 24.
        if len(chunk) < 40:
            raise InvalidAudioError("WAVE_FORMAT_EXTENSIBLE 'fmt ' chunk is truncated")
        (audio_format,) = struct.unpack_from("<H", chunk, 24)
    return audio_format, channels, sample_rate, block_align, bits


def _int16_to_float(data: bytes) -> np.ndarray:
    """Convert little-endian int16 samples to float32 in [-1, 1)."""
    samples = np.frombuffer(data, dtype="<i2")
    return samples.astype(np.float32) / 32768.0


def _parse_content_type(value: str | None) -> tuple[str | None, dict[str, str]]:
    """Split a Content-Type header into a lowercase media type and its parameters."""
    if not value or not value.strip():
        return None, {}
    media_type, *raw_params = value.split(";")
    params = {}
    for raw in raw_params:
        key, sep, val = raw.partition("=")
        if sep:
            params[key.strip().lower()] = val.strip().strip('"').lower()
    return media_type.strip().lower() or None, params
