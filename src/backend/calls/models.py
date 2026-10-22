"""
Declare and configure the models for the calls application.

A call is one instance of a LiveKit room: LiveKit creates the room when the
first participant joins and closes it shortly after the last one leaves. Each
instance has its own sid ("RM_..."), so a room hosts a new call every time it
is used. Calls are fed by LiveKit webhooks (sessions and participants) and by
the clients, which post the chat messages they send (checked with LiveKit).
"""

from django.conf import settings
from django.db import models
from django.utils.translation import gettext_lazy as _

from core.models import BaseModel, Room


class CallSession(BaseModel):
    """One LiveKit room instance (one sid), from its start to its end."""

    room = models.ForeignKey(
        Room,
        on_delete=models.CASCADE,
        related_name="call_sessions",
        verbose_name=_("Room"),
    )
    livekit_sid = models.CharField(
        max_length=64,
        unique=True,
        verbose_name=_("LiveKit room sid"),
        help_text=_("Sid of the LiveKit room instance, as LiveKit gives it."),
    )
    title = models.CharField(
        max_length=255,
        blank=True,
        default="",
        verbose_name=_("Title"),
        help_text=_("Title of the room when the call started."),
    )
    started_at = models.DateTimeField(db_index=True, verbose_name=_("Started at"))
    ended_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Ended at"))
    last_activity_at = models.DateTimeField(
        db_index=True,
        verbose_name=_("Last activity at"),
        help_text=_("Start of the call or time of its last chat message."),
    )

    class Meta:
        db_table = "meet_call_session"
        ordering = ("-last_activity_at",)
        verbose_name = _("Call")
        verbose_name_plural = _("Calls")
        indexes = [
            models.Index(
                fields=["room", "-started_at"], name="call_session_room_started_idx"
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["room"],
                condition=models.Q(ended_at__isnull=True),
                name="unique_open_call_session_per_room",
            )
        ]

    def __str__(self):
        return f"Call {self.id} in room {self.room_id}"

    @property
    def is_active(self) -> bool:
        """Whether the call is still going on."""
        return self.ended_at is None


class CallParticipant(BaseModel):
    """A person (user, guest or phone caller) who joined a call.

    Agents, egress and ingress participants are not recorded. A participant
    who leaves and comes back keeps one row: `joined_at` is the first join,
    `last_joined_at` the latest one and `left_at` the latest departure (empty
    while present).
    """

    session = models.ForeignKey(
        CallSession,
        on_delete=models.CASCADE,
        related_name="participants",
        verbose_name=_("Call"),
    )
    identity = models.CharField(max_length=255, verbose_name=_("LiveKit identity"))
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="call_participations",
        verbose_name=_("User"),
    )
    name = models.CharField(
        max_length=255, blank=True, default="", verbose_name=_("Display name")
    )
    is_guest = models.BooleanField(default=True, verbose_name=_("Guest"))
    joined_at = models.DateTimeField(verbose_name=_("Joined at"))
    last_joined_at = models.DateTimeField(verbose_name=_("Last joined at"))
    left_at = models.DateTimeField(null=True, blank=True, verbose_name=_("Left at"))

    class Meta:
        db_table = "meet_call_participant"
        ordering = ("joined_at", "id")
        verbose_name = _("Call participant")
        verbose_name_plural = _("Call participants")
        indexes = [
            models.Index(fields=["user", "session"], name="call_participant_user_idx"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "identity"], name="unique_call_participant"
            )
        ]

    def __str__(self):
        return f"{self.name or self.identity} in call {self.session_id}"


class ChatMessage(BaseModel):
    """A chat message sent during a call, as posted by its sender's client.

    The sender is the one LiveKit showed connected to the call, under the name
    LiveKit knew.
    """

    session = models.ForeignKey(
        CallSession,
        on_delete=models.CASCADE,
        related_name="messages",
        verbose_name=_("Call"),
    )
    client_id = models.CharField(
        max_length=64,
        verbose_name=_("Client id"),
        help_text=_(
            "Id of the LiveKit text stream, the same for every client: unique "
            "per sender only."
        ),
    )
    sender_identity = models.CharField(
        max_length=255, verbose_name=_("Sender LiveKit identity")
    )
    sender_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name=_("Sender"),
    )
    sender_name = models.CharField(
        max_length=255, blank=True, default="", verbose_name=_("Sender name")
    )
    text = models.TextField(verbose_name=_("Text"))
    sent_at = models.DateTimeField(verbose_name=_("Sent at"))

    class Meta:
        db_table = "meet_call_chat_message"
        ordering = ("sent_at", "created_at")
        verbose_name = _("Chat message")
        verbose_name_plural = _("Chat messages")
        indexes = [
            models.Index(
                fields=["session", "sent_at"], name="call_chat_message_sent_idx"
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "sender_identity", "client_id"],
                name="unique_chat_message_sender_client_id",
            )
        ]

    def __str__(self):
        return f"Message {self.client_id} in call {self.session_id}"


class CallUserState(models.Model):
    """What a user has read of a call."""

    session = models.ForeignKey(
        CallSession,
        on_delete=models.CASCADE,
        related_name="user_states",
        verbose_name=_("Call"),
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="+",
        verbose_name=_("User"),
    )
    last_read_at = models.DateTimeField(
        null=True, blank=True, verbose_name=_("Last read at")
    )

    class Meta:
        db_table = "meet_call_user_state"
        verbose_name = _("Call read state")
        verbose_name_plural = _("Call read states")
        constraints = [
            models.UniqueConstraint(
                fields=["session", "user"], name="unique_call_user_state"
            )
        ]

    def __str__(self):
        return f"Read state of user {self.user_id} in call {self.session_id}"


class CallRoomTitle(models.Model):
    """Human title of a room, given its next calls (e.g. a scheduled meeting).

    It is kept out of `Room.name`, which gives the room its slug (its URL).
    """

    room = models.OneToOneField(
        Room,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="call_title",
        verbose_name=_("Room"),
    )
    title = models.CharField(max_length=255, verbose_name=_("Title"))
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
        db_table = "meet_call_room_title"
        verbose_name = _("Room call title")
        verbose_name_plural = _("Room call titles")

    def __str__(self):
        return self.title
