"""Serializers of the transcripts application."""

# pylint: disable=abstract-method

from django.urls import reverse

from rest_framework import serializers

from core.models import Room

from . import asr
from .models import Transcript, TranscriptFormat, TranscriptStatusChoices

# Largest value of the PositiveIntegerField "segments_count" (PostgreSQL integer)
MAX_SEGMENTS_COUNT = 2**31 - 1

# Same limit as the "asr_model" field of RoomTranscriptionSettings
MAX_ASR_MODEL_LENGTH = 64
# The agent reports each model once; the ASR service offers a handful of them
MAX_ASR_MODELS = 20


class TranscriptRoomSerializer(serializers.ModelSerializer):
    """Room of a transcript, as listed to its users."""

    class Meta:
        model = Room
        fields = ["id", "name", "slug"]
        read_only_fields = fields


class TranscriptSerializer(serializers.ModelSerializer):
    """Read-only transcript, with the API paths to download its files."""

    room = TranscriptRoomSerializer(read_only=True)
    is_ready = serializers.BooleanField(read_only=True)
    downloads = serializers.SerializerMethodField()
    models = serializers.ListField(
        source="asr_models", child=serializers.CharField(), read_only=True
    )

    class Meta:
        model = Transcript
        fields = [
            "id",
            "room",
            "status",
            "language",
            "started_at",
            "ended_at",
            "created_at",
            "participants",
            "segments_count",
            "models",
            "is_ready",
            "downloads",
        ]
        read_only_fields = fields

    def get_downloads(self, obj) -> dict:
        """API paths of the downloadable files, null while they are not available."""
        download_path = reverse("transcripts-download", kwargs={"pk": obj.pk})
        return {
            file_format: (
                f"{download_path}?format={file_format}"
                if obj.is_ready and obj.get_file_key(file_format)
                else None
            )
            for file_format in (TranscriptFormat.MD.value, TranscriptFormat.JSON.value)
        }


class AgentHookParticipantSerializer(serializers.Serializer):
    """A participant who spoke, as reported by the agent."""

    identity = serializers.CharField()
    name = serializers.CharField(
        allow_blank=True, allow_null=True, required=False, default=""
    )


class AgentHookFilesSerializer(serializers.Serializer):
    """S3 keys of the final transcript files written by the agent."""

    # Same limit as the model fields, checked before the hook changes anything
    json = serializers.CharField(
        allow_null=True, required=False, default=None, max_length=255
    )
    md = serializers.CharField(
        allow_null=True, required=False, default=None, max_length=255
    )


class AgentHookSerializer(serializers.Serializer):
    """Result of a transcription, posted by the agent when it is finalized."""

    transcript_id = serializers.UUIDField()
    # LiveKit room name the agent works in, checked against the transcript room
    room_id = serializers.CharField(max_length=255)
    status = serializers.ChoiceField(
        choices=[TranscriptStatusChoices.READY, TranscriptStatusChoices.FAILED]
    )
    started_at = serializers.DateTimeField(allow_null=True, required=False)
    ended_at = serializers.DateTimeField(allow_null=True, required=False)
    participants = AgentHookParticipantSerializer(many=True, required=False)
    segments_count = serializers.IntegerField(
        min_value=0, max_value=MAX_SEGMENTS_COUNT, required=False, default=0
    )
    files = AgentHookFilesSerializer(allow_null=True, required=False)
    error = serializers.CharField(allow_null=True, allow_blank=True, required=False)
    # Ids of the ASR models that recognized the phrases, in order of first use
    models = serializers.ListField(
        child=serializers.CharField(max_length=MAX_ASR_MODEL_LENGTH),
        allow_null=True,
        required=False,
        max_length=MAX_ASR_MODELS,
    )

    def validate(self, attrs):
        """A ready transcript must come with both of its files."""
        files = attrs.get("files") or {}
        if attrs["status"] == TranscriptStatusChoices.READY and not (
            files.get("json") and files.get("md")
        ):
            raise serializers.ValidationError(
                {"files": "Both the json and md keys are required when ready."}
            )
        return attrs


class RoomTranscriptionSettingsSerializer(serializers.Serializer):
    """Recognition model of a room: an id listed by the ASR service, or null.

    Null resets the room to the default model of the ASR service. Validating
    an id reads the list of models, which raises AsrModelsUnavailableError
    when the ASR service cannot be reached.
    """

    asr_model = serializers.CharField(max_length=MAX_ASR_MODEL_LENGTH, allow_null=True)

    def validate_asr_model(self, value):
        """The model must be offered by the ASR service and not have failed."""
        if value is None:
            return None

        model = asr.find_model(asr.get_models(), value)
        if model is None:
            raise serializers.ValidationError("Unknown recognition model.")
        if not asr.is_selectable(model):
            raise serializers.ValidationError(
                "This recognition model is unavailable: it failed to load."
            )
        return value
