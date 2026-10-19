"""Tests of the WAV / raw PCM decoder."""

import struct

import numpy as np
import pytest

from asr_service.audio import InvalidAudioError, UnsupportedAudioError, decode_audio

from .conftest import pcm16, tone, wav_custom, wav_float32, wav_pcm16


def test_wav_pcm16_is_decoded_to_float32():
    """16-bit PCM WAV becomes float32 samples in [-1, 1]."""
    samples = tone(0.5)
    decoded = decode_audio(wav_pcm16(samples), "audio/wav")
    assert decoded.dtype == np.float32
    assert decoded.shape == samples.shape
    np.testing.assert_allclose(decoded, samples, atol=1e-4)


@pytest.mark.parametrize(
    "content_type", ["audio/x-wav", "audio/wave", "Audio/WAV; codecs=1"]
)
def test_wav_content_type_aliases(content_type):
    """All usual WAV media types are accepted, case-insensitively."""
    assert decode_audio(wav_pcm16(tone(0.2)), content_type).shape == (3200,)


def test_wav_float32_is_decoded():
    """32-bit IEEE float WAV is accepted and clipped to [-1, 1]."""
    samples = np.array([0.0, 0.25, -0.5, 1.5, -2.0], dtype=np.float32)
    decoded = decode_audio(wav_float32(samples), "audio/wav")
    np.testing.assert_array_equal(decoded, [0.0, 0.25, -0.5, 1.0, -1.0])


def test_wav_extensible_pcm_and_extra_chunks():
    """WAVE_FORMAT_EXTENSIBLE and unknown chunks (odd sized, padded) are handled."""
    data = pcm16(tone(0.1))
    extra = b"LIST" + struct.pack("<I", 3) + b"abc" + b"\x00"
    body = wav_custom(data, extensible_subformat=1, extra_chunks=extra)
    assert decode_audio(body, "audio/wav").shape == (1600,)


def test_wav_with_streaming_size_and_truncated_data():
    """A data size of 0xFFFFFFFF or a trailing half sample does not break decoding."""
    body = bytearray(wav_custom(pcm16(tone(0.1)) + b"\x01"))
    size_offset = body.index(b"data") + 4
    body[size_offset : size_offset + 4] = struct.pack("<I", 0xFFFFFFFF)
    assert decode_audio(bytes(body), "audio/wav").shape == (1600,)


def test_raw_pcm_variants():
    """Raw s16le PCM is accepted under several media types."""
    samples = tone(0.25)
    for content_type in (
        "audio/pcm",
        "application/octet-stream",
        "audio/L16; rate=16000; channels=1",
    ):
        decoded = decode_audio(pcm16(samples), content_type)
        np.testing.assert_allclose(decoded, samples, atol=1e-4)


def test_missing_content_type_is_sniffed():
    """Without Content-Type, RIFF bodies are WAV and the rest is raw PCM."""
    assert decode_audio(wav_pcm16(tone(0.1)), None).shape == (1600,)
    assert decode_audio(pcm16(tone(0.1)), "").shape == (1600,)


@pytest.mark.parametrize(
    ("body", "content_type", "message"),
    [
        (wav_pcm16(tone(0.1, rate=8000), rate=8000), "audio/wav", "sample rate 8000"),
        (wav_pcm16(tone(0.1), channels=2), "audio/wav", "channel count 2"),
        (wav_custom(b"\x00" * 300, bits=24), "audio/wav", "24-bit PCM"),
        (wav_custom(b"\x00" * 300, format_tag=3, bits=64), "audio/wav", "64-bit float"),
        (wav_custom(b"\x00" * 300, format_tag=7, bits=8), "audio/wav", "mu-law"),
        (wav_custom(b"\x00" * 300, extensible_subformat=0x55), "audio/wav", "MP3"),
        (pcm16(tone(0.1)), "audio/pcm; rate=48000", "sample rate 48000"),
        (pcm16(tone(0.1)), "audio/l16; channels=2", "channel count 2"),
        (b"ID3\x03fake mp3", "audio/mpeg", "Unsupported Content-Type 'audio/mpeg'"),
    ],
)
def test_unsupported_formats(body, content_type, message):
    """Valid audio in another format is a 422 with an explicit message."""
    with pytest.raises(UnsupportedAudioError, match=message) as excinfo:
        decode_audio(body, content_type)
    assert excinfo.value.status_code == 422


@pytest.mark.parametrize(
    ("body", "content_type", "message"),
    [
        (b"", "audio/wav", "Empty"),
        (b"this is definitely not audio", "audio/wav", "not a RIFF/WAVE"),
        (b"RIFF\x00\x00\x00\x00WAVE", "audio/wav", "no 'fmt '"),
        (
            b"RIFF\x00\x00\x00\x00WAVEfmt \x04\x00\x00\x00\x01\x00",
            "audio/wav",
            "truncated",
        ),
        (wav_custom(b"")[:36], "audio/wav", "no 'data'"),
        (b"\x01\x02\x03", "audio/pcm", "whole number of 16-bit samples"),
        (wav_float32(np.array([0.0, np.nan], dtype=np.float32)), "audio/wav", "NaN"),
    ],
)
def test_garbage_is_rejected(body, content_type, message):
    """Undecodable bodies are a 400."""
    with pytest.raises(InvalidAudioError, match=message) as excinfo:
        decode_audio(body, content_type)
    assert excinfo.value.status_code == 400
