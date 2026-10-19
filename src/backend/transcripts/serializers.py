"""Serializers of the transcripts application."""

# pylint: disable=abstract-method

from django.urls import reverse

from rest_framework import serializers

from core.models import Room

from .models import Transcript, TranscriptFormat, TranscriptStatusChoices

# Largest value of the PositiveIntegerField "segments_count" (PostgreSQL integer)
MAX_SEGMENTS_COUNT = 2**31 - 1


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
