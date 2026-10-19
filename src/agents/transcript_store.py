"""Per-participant transcript storage for the multi-user transcriber.

The final phrases of a transcription are kept in memory and written to S3
incrementally, as small immutable "part" objects, so that a crash of the agent
loses at most ``TRANSCRIPT_FLUSH_INTERVAL_S`` seconds of speech; a restarted
job resumes from the parts. When the transcription stops (or the call ends),
all the parts are merged into ``transcript.json`` and ``transcript.md`` and the
backend is notified through its agent hook.

S3 layout (bucket ``AWS_STORAGE_BUCKET_NAME``, prefix ``TRANSCRIPTION_S3_PREFIX``)::

    <prefix>/<transcript id>/parts/<epoch ms, 13 digits>-<seq, 6 digits>.json
    <prefix>/<transcript id>/transcript.json
    <prefix>/<transcript id>/transcript.md
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import boto3
import httpx
from botocore.config import Config

logger = logging.getLogger("transcript-store")

FORMAT_VERSION = 1
# phrases whose speech started shortly before the store was opened are kept
START_TOLERANCE_S = 1.0
HOOK_TIMEOUT_S = 10.0
# a shorter flush period would keep the event loop (audio, VAD) busy
MIN_FLUSH_INTERVAL_S = 0.5

# (timeout_s, started_before epoch s) -> awaitable; waits for in-flight phrases
WaitPending = Callable[..., Awaitable[Any]]


def create_s3_client():
    """Create an S3 client for the configured endpoint and region.

    Copy of ``metadata_collector.create_s3_client`` (importing that module
    would build its AgentServer). The endpoint may be given with or without a
    scheme: the scheme always follows AWS_S3_SECURE_ACCESS.
    """
    endpoint = (
        os.getenv("AWS_S3_ENDPOINT_URL", "")
        .removeprefix("https://")
        .removeprefix("http://")
        .rstrip("/")
    )
    secure = os.getenv("AWS_S3_SECURE_ACCESS", "False").lower() == "true"

    return boto3.client(
        "s3",
        endpoint_url=f"{'https' if secure else 'http'}://{endpoint}",
        aws_access_key_id=os.getenv("AWS_S3_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_S3_SECRET_ACCESS_KEY"),
        region_name=os.getenv("AWS_S3_REGION_NAME"),
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


def format_timestamp(ms: int) -> str:
    """Format epoch milliseconds as ISO 8601 UTC, e.g. 2026-10-19T10:00:00.000Z."""
    moment = datetime.fromtimestamp(ms // 1000, tz=UTC)
    return f"{moment:%Y-%m-%dT%H:%M:%S}.{ms % 1000:03d}Z"


def parse_timestamp(value: str) -> int:
    """Parse an ISO 8601 date-time (naive means UTC) into epoch milliseconds."""
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return round(moment.timestamp() * 1000)


@dataclass(frozen=True)
class Segment:
    """One final phrase of one participant (times in epoch milliseconds)."""

    identity: str
    name: str
    start_ms: int
    end_ms: int
    text: str

    def to_json(self) -> dict:
        """Serialize the segment as in transcript.json."""
        return {
            "identity": self.identity,
            "name": self.name,
            "start": format_timestamp(self.start_ms),
            "end": format_timestamp(self.end_ms),
            "text": self.text,
        }

    @classmethod
    def from_json(cls, data: dict) -> Segment:
        """Build a segment from its serialized form."""
        return cls(
            identity=str(data["identity"]),
            name=str(data.get("name") or data["identity"]),
            start_ms=parse_timestamp(data["start"]),
            end_ms=parse_timestamp(data["end"]),
            text=str(data["text"]),
        )


def merge_segments(segments: list[Segment]) -> list[Segment]:
    """Drop identical segments and sort them by start time."""
    return sorted(
        dict.fromkeys(segments), key=lambda s: (s.start_ms, s.end_ms, s.identity)
    )


def list_participants(segments: list[Segment]) -> list[dict]:
    """Participants who spoke, in order of first phrase, with their last name."""
    names: dict[str, str] = {}
    for segment in segments:
        # re-assigning a key keeps its position: the order of first phrase
        names[segment.identity] = segment.name
    return [{"identity": identity, "name": name} for identity, name in names.items()]


def _one_line(value: str) -> str:
    return " ".join(value.split())


def _markdown_name(name: str) -> str:
    return _one_line(name).replace("\\", "\\\\").replace("*", "\\*")


def _zone(name: str) -> tuple[Any, str]:
    try:
        return ZoneInfo(name), name
    except (ZoneInfoNotFoundError, ValueError):
        logger.warning("unknown TRANSCRIPT_TIMEZONE %r, using UTC", name)
        return UTC, "UTC"


def render_markdown(  # noqa: PLR0913
    *,
    room_name: str,
    started_ms: int,
    ended_ms: int,
    participants: list[dict],
    segments: list[Segment],
    timezone: str,
) -> str:
    """Render the human-readable transcript (in Russian).

    Each phrase is a separate line ``[hh:mm:ss] **Name**: text``, where the
    time is the offset of its start from the beginning of the transcription.
    """
    zone, zone_name = _zone(timezone)
    start = datetime.fromtimestamp(started_ms / 1000, tz=zone)
    end = datetime.fromtimestamp(max(ended_ms, started_ms) / 1000, tz=zone)
    if start.date() == end.date():
        date = f"{start:%d.%m.%Y}, {start:%H:%M}–{end:%H:%M}"
    else:
        date = f"{start:%d.%m.%Y, %H:%M} – {end:%d.%m.%Y, %H:%M}"
    names = ", ".join(_markdown_name(p["name"]) for p in participants) or "—"

    lines = [
        f"# Расшифровка встречи «{_one_line(room_name)}»",
        "",
        f"- Дата: {date} ({zone_name})",
        f"- Участники: {names}",
    ]
    for segment in segments:
        offset = max(0, segment.start_ms - started_ms) // 1000
        hours, rest = divmod(offset, 3600)
        minutes, seconds = divmod(rest, 60)
        # a blank line between phrases keeps one phrase per line once rendered
        lines.append("")
        lines.append(
            f"[{hours:02d}:{minutes:02d}:{seconds:02d}] "
            f"**{_markdown_name(segment.name)}**: {_one_line(segment.text)}"
        )
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class StoreConfig:
    """Settings of the transcript storage."""

    bucket: str
    hook_url: str
    hook_token: str = ""
    prefix: str = "transcriptions"
    flush_interval_s: float = 5.0
    finalize_grace_s: float = 5.0
    timezone: str = "Europe/Moscow"
    language: str = "ru"

    @classmethod
    def from_env(cls) -> StoreConfig | None:
        """Read the settings; None when the storage is not configured."""
        bucket = os.getenv("AWS_STORAGE_BUCKET_NAME", "")
        hook_url = os.getenv("TRANSCRIPTION_HOOK_URL", "")
        if not bucket or not hook_url:
            return None
        return cls(
            bucket=bucket,
            hook_url=hook_url,
            hook_token=os.getenv("TRANSCRIPTION_AGENT_TOKEN", ""),
            prefix=os.getenv("TRANSCRIPTION_S3_PREFIX", "transcriptions").strip("/"),
            flush_interval_s=max(
                MIN_FLUSH_INTERVAL_S,
                float(os.getenv("TRANSCRIPT_FLUSH_INTERVAL_S", "5")),
            ),
            finalize_grace_s=float(os.getenv("TRANSCRIPT_FINALIZE_GRACE_S", "5")),
            timezone=os.getenv("TRANSCRIPT_TIMEZONE", "Europe/Moscow"),
            language=os.getenv("TRANSCRIPTION_LANGUAGE", "ru"),
        )


class AgentHook:
    """Notify the backend that a transcript is ready or failed."""

    def __init__(
        self,
        url: str,
        token: str,
        *,
        max_retries: int = 5,
        backoff_s: float = 0.5,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Configure the hook.

        Args:
            url: URL of the backend agent hook.
            token: Bearer token shared with the backend.
            max_retries: Retries on network errors and 5xx responses.
            backoff_s: First retry delay, doubled at each retry (max 8 s).
            transport: Optional HTTP transport (tests).
        """
        self.url = url
        self._token = token
        self._max_retries = max_retries
        self._backoff_s = backoff_s
        self._transport = transport

    async def notify(self, payload: dict) -> bool:
        """POST the payload; return True once the backend accepted it."""
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        body = json.dumps(payload, ensure_ascii=False).encode()
        async with httpx.AsyncClient(
            transport=self._transport, timeout=HOOK_TIMEOUT_S
        ) as client:
            for attempt in range(self._max_retries + 1):
                retryable = True
                try:
                    response = await client.post(
                        self.url, content=body, headers=headers
                    )
                    if response.is_success:
                        return True
                    retryable = response.status_code in (408, 429) or (
                        response.status_code >= httpx.codes.INTERNAL_SERVER_ERROR
                    )
                    error = f"HTTP {response.status_code}: {response.text[:200]!r}"
                except httpx.HTTPError as exc:
                    error = repr(exc)
                if not retryable or attempt == self._max_retries:
                    logger.error(
                        "transcript hook failed for %s: %s",
                        payload.get("transcript_id"),
                        error,
                    )
                    return False
                delay = min(self._backoff_s * 2**attempt, 8.0)
                logger.warning(
                    "transcript hook failed (%s), retrying in %.1fs", error, delay
                )
                await asyncio.sleep(delay)
        return False


class TranscriptStore:
    """Segments of one transcription, persisted to S3 as they arrive."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        transcript_id: str,
        room_id: str,
        room_name: str,
        started_at_ms: int | None,
        config: StoreConfig,
        s3_client,
        hook: AgentHook | None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        """Init the store (call ``open`` before use).

        Args:
            transcript_id: Backend Transcript id.
            room_id: LiveKit room name (the backend Room id).
            room_name: Human-readable room name.
            started_at_ms: Start of the transcription (epoch ms), if known.
            config: Storage settings.
            s3_client: boto3 S3 client (synchronous, used in threads).
            hook: Backend notification, None to skip it.
            clock: Wall-clock source in epoch seconds (tests).
        """
        self.transcript_id = transcript_id
        self.room_id = room_id
        self.room_name = room_name or room_id
        self._config = config
        self._s3 = s3_client
        self._hook = hook
        self._clock = clock
        self.opened_at = clock()
        self.started_at_ms = (
            started_at_ms if started_at_ms is not None else round(self.opened_at * 1000)
        )
        self.stop_at: float | None = None
        self._segments: list[Segment] = []  # all known segments
        self._unflushed: list[Segment] = []  # not written to S3 yet
        self._seq = 0
        self._flush_lock = asyncio.Lock()
        self._flusher: asyncio.Task | None = None
        self._finalize_task: asyncio.Task | None = None
        self._accepting = True

    @property
    def key_prefix(self) -> str:
        """S3 key prefix of this transcription."""
        return f"{self._config.prefix}/{self.transcript_id}"

    @property
    def segments(self) -> list[Segment]:
        """Known segments, merged and sorted."""
        return merge_segments(self._segments)

    async def open(self) -> None:
        """Resume from the parts already in S3, then start the periodic flush."""
        try:
            resumed, last_seq = await asyncio.to_thread(self._read_parts)
        except Exception:
            logger.exception("could not resume transcript %s", self.transcript_id)
        else:
            self._segments.extend(resumed)
            self._seq = last_seq + 1
            if resumed:
                logger.info(
                    "resumed transcript %s with %d segments",
                    self.transcript_id,
                    len(resumed),
                )
        if self._flusher is None:
            self._flusher = asyncio.create_task(
                self._flush_loop(), name=f"transcript_flush_{self.transcript_id}"
            )

    def accepts(self, start: float) -> bool:
        """Whether a phrase whose speech started at ``start`` belongs here."""
        if not self._accepting or start < self.opened_at - START_TOLERANCE_S:
            return False
        return self.stop_at is None or start <= self.stop_at

    def add_segment(
        self, *, identity: str, name: str, start: float, end: float, text: str
    ) -> bool:
        """Buffer a final phrase (wall-clock times in epoch seconds)."""
        text = _one_line(text)
        if not text or not self.accepts(start):
            return False
        start_ms = round(start * 1000)
        segment = Segment(
            identity=identity,
            name=_one_line(name) or identity,
            start_ms=start_ms,
            end_ms=max(start_ms, round(end * 1000)),
            text=text,
        )
        self._segments.append(segment)
        self._unflushed.append(segment)
        return True

    async def flush(self) -> bool:
        """Write the buffered segments as a new part; False if it failed."""
        async with self._flush_lock:
            if not self._unflushed:
                return True
            batch = list(self._unflushed)
            key = (
                f"{self.key_prefix}/parts/"
                f"{round(self._clock() * 1000):013d}-{self._seq:06d}.json"
            )
            body = json.dumps([s.to_json() for s in batch], ensure_ascii=False)
            try:
                await asyncio.to_thread(
                    self._s3.put_object,
                    Bucket=self._config.bucket,
                    Key=key,
                    Body=body.encode(),
                    ContentType="application/json",
                )
            except Exception:
                logger.warning(
                    "could not write transcript part %s, will retry", key, exc_info=True
                )
                return False
            self._seq += 1
            del self._unflushed[: len(batch)]
            return True

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(self._config.flush_interval_s)
            await self.flush()

    def _read_parts(self) -> tuple[list[Segment], int]:
        """Read every part (blocking). Returns the segments and the last seq."""
        prefix = f"{self.key_prefix}/parts/"
        keys: list[str] = []
        kwargs = {"Bucket": self._config.bucket, "Prefix": prefix}
        while True:
            response = self._s3.list_objects_v2(**kwargs)
            keys.extend(item["Key"] for item in response.get("Contents", []))
            if not response.get("IsTruncated"):
                break
            kwargs["ContinuationToken"] = response["NextContinuationToken"]

        segments: list[Segment] = []
        last_seq = -1
        for key in sorted(keys):
            name = key.removeprefix(prefix).removesuffix(".json")
            _, _, seq = name.partition("-")
            if seq.isdigit():
                last_seq = max(last_seq, int(seq))
            body = self._s3.get_object(Bucket=self._config.bucket, Key=key)["Body"]
            try:
                segments.extend(
                    Segment.from_json(item) for item in json.loads(body.read())
                )
            except (ValueError, KeyError, TypeError):
                logger.warning("ignoring malformed transcript part %s", key)
        return segments, last_seq

    def finalize(self, *, wait_pending: WaitPending | None = None) -> asyncio.Task:
        """Stop the transcription and write the final files (idempotent).

        Args:
            wait_pending: Awaitable ``(timeout_s, started_before=epoch_s)``
                that waits for the phrases still being recognized.

        Returns:
            The finalization task; its result is True when the transcript
            is ready, False when it failed.
        """
        if self._finalize_task is None:
            self.stop_at = self._clock()
            self._finalize_task = asyncio.create_task(
                self._finalize(wait_pending),
                name=f"transcript_finalize_{self.transcript_id}",
            )
        return self._finalize_task

    async def _finalize(self, wait_pending: WaitPending | None) -> bool:
        grace = self._config.finalize_grace_s
        if wait_pending is not None and grace > 0:
            try:
                await asyncio.wait_for(
                    wait_pending(grace, started_before=self.stop_at), timeout=grace + 1
                )
            except Exception:
                logger.warning("waiting for pending phrases failed", exc_info=True)
        self._accepting = False
        if self._flusher is not None:
            self._flusher.cancel()
            await asyncio.wait([self._flusher])

        ended_ms = round((self.stop_at or self._clock()) * 1000)
        files: dict[str, str | None] = {"json": None, "md": None}
        error = None
        segments = merge_segments(self._segments)
        try:
            # best effort: the final files hold every known segment anyway
            await self.flush()
            stored, _ = await asyncio.to_thread(self._read_parts)
            segments = merge_segments(stored + self._segments)
            files = await asyncio.to_thread(self._write_final, segments, ended_ms)
        except Exception as exc:
            logger.exception("could not finalize transcript %s", self.transcript_id)
            error = f"{type(exc).__name__}: {exc}"

        participants = list_participants(segments)
        status = "failed" if error else "ready"
        logger.info(
            "transcript %s %s: %d segments, %d participants",
            self.transcript_id,
            status,
            len(segments),
            len(participants),
        )
        if self._hook is not None:
            await self._hook.notify(
                {
                    "transcript_id": self.transcript_id,
                    "room_id": self.room_id,
                    "status": status,
                    "started_at": format_timestamp(self.started_at_ms),
                    "ended_at": format_timestamp(ended_ms),
                    "participants": participants,
                    "segments_count": len(segments),
                    "files": files,
                    "error": error,
                }
            )
        return error is None

    def _write_final(self, segments: list[Segment], ended_ms: int) -> dict:
        """Write transcript.json and transcript.md (blocking)."""
        participants = list_participants(segments)
        document = {
            "version": FORMAT_VERSION,
            "transcript_id": self.transcript_id,
            "language": self._config.language,
            "room": {"id": self.room_id, "name": self.room_name},
            "started_at": format_timestamp(self.started_at_ms),
            "ended_at": format_timestamp(ended_ms),
            "participants": participants,
            "segments": [s.to_json() for s in segments],
        }
        markdown = render_markdown(
            room_name=self.room_name,
            started_ms=self.started_at_ms,
            ended_ms=ended_ms,
            participants=participants,
            segments=segments,
            timezone=self._config.timezone,
        )
        json_key = f"{self.key_prefix}/transcript.json"
        md_key = f"{self.key_prefix}/transcript.md"
        self._s3.put_object(
            Bucket=self._config.bucket,
            Key=json_key,
            Body=json.dumps(document, ensure_ascii=False, indent=2).encode(),
            ContentType="application/json",
        )
        self._s3.put_object(
            Bucket=self._config.bucket,
            Key=md_key,
            Body=markdown.encode(),
            ContentType="text/markdown; charset=utf-8",
        )
        return {"json": json_key, "md": md_key}
