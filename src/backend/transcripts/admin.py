"""Admin classes and registrations for the transcripts application."""

from django.contrib import admin

from .models import Transcript


@admin.register(Transcript)
class TranscriptAdmin(admin.ModelAdmin):
    """Transcript admin interface declaration."""

    autocomplete_fields = ["started_by", "users"]
    list_display = (
        "id",
        "status",
        "room",
        "started_by",
        "started_at",
        "ended_at",
        "segments_count",
    )
    list_filter = ["status", "created_at"]
    list_select_related = ("room", "started_by")
    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
        "room",
        "started_at",
        "ended_at",
        "participants",
        "segments_count",
        "json_key",
        "md_key",
        "dispatch_id",
        "error",
        "asr_models",
    )
    search_fields = [
        "=id",
        "room__slug",
        "=room__id",
        "started_by__email",
    ]
