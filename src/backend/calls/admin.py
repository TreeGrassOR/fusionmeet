"""Admin classes and registrations for the calls application.

Calls are written by LiveKit webhooks and clients only: the admin shows them
for support and cannot add or change them.
"""

from django.contrib import admin

from .models import CallParticipant, CallRoomTitle, CallSession, ChatMessage


class ReadOnlyInline(admin.TabularInline):
    """Inline that lists rows without editing them."""

    extra = 0
    can_delete = False
    show_change_link = False

    def has_add_permission(self, request, obj=None):
        """Rows are never added from the admin."""
        return False

    def has_change_permission(self, request, obj=None):
        """Rows are never changed from the admin."""
        return False


class CallParticipantInline(ReadOnlyInline):
    """Participants of a call."""

    model = CallParticipant
    fields = ("name", "identity", "user", "is_guest", "joined_at", "left_at")
    readonly_fields = fields


class ChatMessageInline(ReadOnlyInline):
    """Chat messages of a call."""

    model = ChatMessage
    fields = ("sent_at", "sender_name", "sender_identity", "text")
    readonly_fields = fields


@admin.register(CallSession)
class CallSessionAdmin(admin.ModelAdmin):
    """Call admin interface declaration (read-only)."""

    inlines = [CallParticipantInline, ChatMessageInline]
    list_display = ("id", "room", "title", "started_at", "ended_at", "livekit_sid")
    list_filter = ["started_at"]
    list_select_related = ("room",)
    readonly_fields = (
        "id",
        "created_at",
        "updated_at",
        "room",
        "livekit_sid",
        "title",
        "started_at",
        "ended_at",
        "last_activity_at",
    )
    search_fields = ["=id", "=livekit_sid", "room__slug", "=room__id", "title"]

    def has_add_permission(self, request):
        """Calls are never added from the admin."""
        return False

    def has_change_permission(self, request, obj=None):
        """Calls are never changed from the admin."""
        return False


@admin.register(CallRoomTitle)
class CallRoomTitleAdmin(admin.ModelAdmin):
    """Room call title admin interface declaration (read-only)."""

    list_display = ("room", "title", "updated_by", "updated_at")
    list_select_related = ("room", "updated_by")
    readonly_fields = ("room", "title", "updated_by", "created_at", "updated_at")
    search_fields = ["room__slug", "=room__id", "title"]

    def has_add_permission(self, request):
        """Titles are set from the application only."""
        return False

    def has_change_permission(self, request, obj=None):
        """Titles are set from the application only."""
        return False
