"""
Declare and configure the models for the transcripts application.

A transcript is produced by the transcriber agent, from the audio track of each
participant, while a transcription runs in a room. It is independent from the
`core.Recording` model: a room can be recorded and transcribed at the same time.
"""

import hashlib
import hmac
import re
from datetime import timezone as dt_timezone

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from core.models import BaseModel, RoleChoices, Room

# One segment of an S3 key written by the agent: no "/", no "\", no spaces.
KEY_SEGMENT_PATTERN = re.compile(r"[A-Za-z0-9._-]+")


class TranscriptStatusChoices(models.TextChoices):
    """Lifecycle of a transcript.

    ACTIVE -> STOPPING -> READY | FAILED. The agent reports READY or FAILED
    through the agent hook, either after a stop request or when the call ends.
    """

    ACTIVE = "active", _("Active")
    STOPPING = "stopping", _("Stopping")
    READY = "ready", _("Ready")
    FAILED = "failed", _("Failed")

    @classmethod
    def ongoing(cls):
        """Statuses of a transcript the agent is still writing."""
        return [cls.ACTIVE, cls.STOPPING]


class TranscriptFormat(models.TextChoices):
    """Downloadable transcript files."""

    MD = "md", "Markdown"
    JSON = "json", "JSON"


class Transcript(BaseModel):
    """A per-participant transcription of a meeting held in a room."""

    room = models.ForeignKey(
        Room,
        on_delete=models.CASCADE,
        related_name="transcripts",
        verbose_name=_("Room"),
    )
    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="started_transcripts",
        verbose_name=_("Started by"),
    )
    users = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        blank=True,
        related_name="transcripts",
        verbose_name=_("Users"),
        help_text=_("Users allowed to see and download the transcript."),
    )
    status = models.CharField(
        max_length=20,
        choices=TranscriptStatusChoices.choices,
        default=TranscriptStatusChoices.ACTIVE,
        verbose_name=_("Status"),
    )
    language = models.CharField(
        max_length=10,
        default="ru",
        verbose_name=_("Language"),
    )
    started_at = models.DateTimeField(
        default=timezone.now, verbose_name=_("Started at")
    )
    ended_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Ended at"))
    participants = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_("Participants"),
        help_text=_("Participants who spoke: list of {identity, name}."),
    )
    segments_count = models.PositiveIntegerField(
        default=0, verbose_name=_("Number of segments")
    )
    json_key = models.CharField(
        max_length=255, null=True, blank=True, verbose_name=_("JSON file key")
    )
    md_key = models.CharField(
        max_length=255, null=True, blank=True, verbose_name=_("Markdown file key")
    )
    dispatch_id = models.CharField(
        max_length=255, null=True, blank=True, verbose_name=_("Agent dispatch ID")
    )
    error = models.TextField(blank=True, default="", verbose_name=_("Error"))
    asr_models = models.JSONField(
        default=list,
        blank=True,
        verbose_name=_("Recognition models"),
        help_text=_("Ids of the ASR models that recognized the phrases."),
    )

    class Meta:
        db_table = "meet_transcript"
        ordering = ("-created_at",)
        verbose_name = _("Transcript")
        verbose_name_plural = _("Transcripts")
        constraints = [
            models.UniqueConstraint(
                fields=["room"],
                condition=models.Q(status__in=TranscriptStatusChoices.ongoing()),
                name="unique_ongoing_transcript_per_room",
            )
        ]

    def __str__(self):
        return f"Transcript {self.id} ({self.status})"

    @property
    def is_ready(self) -> bool:
        """Whether the transcript files are available."""
        return self.status == TranscriptStatusChoices.READY

    @property
    def key_prefix(self) -> str:
        """S3 key prefix under which the agent writes this transcript's files."""
        prefix = (settings.TRANSCRIPTION_S3_PREFIX or "").strip("/")
        return f"{prefix}/{self.id}/" if prefix else f"{self.id}/"

    def owns_key(self, key) -> bool:
        """Check that an S3 key belongs to this transcript.

        Keys come from the agent hook. Only keys under the transcript prefix and
        without "." or ".." segments are accepted, so a key can never point to
        another object of the bucket (storages normalize the path).
        """
        if not isinstance(key, str) or not key.startswith(self.key_prefix):
            return False

        segments = key[len(self.key_prefix) :].split("/")
        return all(
            KEY_SEGMENT_PATTERN.fullmatch(segment) and segment not in {".", ".."}
            for segment in segments
        )

    def get_file_key(self, file_format):
        """Return the S3 key of a transcript file, None if unknown or invalid."""
        key = {
            TranscriptFormat.MD: self.md_key,
            TranscriptFormat.JSON: self.json_key,
        }.get(file_format)

        return key if key and self.owns_key(key) else None

    def grant_initial_accesses(self, starter):
        """Give the starter and the room's owners/administrators access.

        Like `core.Recording.grant_initial_accesses`, room roles are copied when
        the transcription starts: later role changes do not affect it.
        """
        privileged_user_ids = set(
            self.room.accesses.filter(
                role__in=[RoleChoices.OWNER, RoleChoices.ADMIN]
            ).values_list("user_id", flat=True)
        )
        privileged_user_ids.discard(None)
        self.users.add(starter, *privileged_user_ids)

    def get_room_metadata(self, status: str) -> dict:
        """Value of the "transcription" key of the LiveKit room metadata."""
        started_by = self.started_by
        return {
            "id": str(self.id),
            "status": status,
            "room_name": self.room.name,
            "started_at": format_timestamp(self.started_at),
            "started_by": (
                (started_by.full_name or started_by.short_name or "")
                if started_by
                else ""
            ),
            "sig": sign_room_transcript(str(self.room_id), self.id),
        }


class RoomTranscriptionSettings(models.Model):
    """Transcription settings of a room, set by the users who can transcribe it.

    A room without settings, or with an empty `asr_model`, uses the default
    model of the ASR service.
    """

    room = models.OneToOneField(
        Room,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="transcription_settings",
        verbose_name=_("Room"),
    )
    asr_model = models.CharField(
        max_length=64,
        blank=True,
        default="",
        verbose_name=_("Recognition model"),
        help_text=_("Id of the ASR model; empty for the default model."),
    )
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("Updated by"),
    )
    created_at = models.DateTimeField(
        verbose_name=_("created on"),
        help_text=_("date and time at which a record was created"),
        auto_now_add=True,
        editable=False,
    )
    updated_at = models.DateTimeField(
        verbose_name=_("updated on"),
        help_text=_("date and time at which a record was last updated"),
        auto_now=True,
        editable=False,
    )

    class Meta:
        db_table = "meet_room_transcription_settings"
        verbose_name = _("Room transcription settings")
        verbose_name_plural = _("Room transcription settings")

    def __str__(self):
        return f"Transcription settings of room {self.room_id}"


def sign_room_transcript(room_name: str, transcript_id) -> str:
    """Sign the transcript id written in the metadata of a LiveKit room.

    Room owners and administrators hold the LiveKit roomAdmin grant and can
    write the room metadata themselves: the agent only follows an entry whose
    signature matches the room it is in, so a room can never point the agent to
    the transcript (and S3 prefix) of another room.

    Returns:
        The hex HMAC-SHA256 of "<LiveKit room name>:<transcript id>", keyed
        with TRANSCRIPTION_AGENT_TOKEN.
    """
    key = (settings.TRANSCRIPTION_AGENT_TOKEN or "").encode()
    message = f"{room_name}:{transcript_id}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def format_timestamp(moment):
    """Format a datetime as ISO 8601 UTC with milliseconds (2026-10-19T10:00:00.000Z)."""
    if moment is None:
        return None
    utc_moment = moment.astimezone(dt_timezone.utc)
    return f"{utc_moment:%Y-%m-%dT%H:%M:%S}.{utc_moment.microsecond // 1000:03d}Z"
