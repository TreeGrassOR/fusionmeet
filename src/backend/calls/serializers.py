"""Serializers of the calls application."""

from django.conf import settings
from django.utils.crypto import salted_hmac

from rest_framework import serializers

from . import queries
from .models import CallRoomTitle, CallSession, ChatMessage
from .services import LIVEKIT_ROOM_SID_PATTERN

# Epoch milliseconds accepted for a message (until year 5138)
MAX_EPOCH_MILLISECONDS = 10**14


def call_title(session) -> str:
    """Title of a call: its own, the room's call title, else a room name that
    is not a generated slug. Empty otherwise: the frontend builds one from the
    participants or the date.
    """
    if session.title:
        return session.title

    room = session.room
    try:
        room_title = room.call_title.title
    except CallRoomTitle.DoesNotExist:
        room_title = ""
    if room_title:
        return room_title

    if room.name and room.name != room.slug:
        return room.name
    return ""


class ChatMessageCreateSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Validate a chat message posted by its sender's client."""

    id = serializers.RegexField(r"^[A-Za-z0-9_-]{1,64}$")
    text = serializers.CharField(
        max_length=2000, allow_blank=False, trim_whitespace=False
    )
    sent_at = serializers.IntegerField(min_value=0, max_value=MAX_EPOCH_MILLISECONDS)
    room_sid = serializers.CharField(
        max_length=64, required=False, allow_null=True, allow_blank=True
    )
    # Accepted but not used: the name stored is the one LiveKit knows
    name = serializers.CharField(
        max_length=255, required=False, allow_null=True, allow_blank=True
    )

    def validate_text(self, value):
        """Refuse a message made of white space only."""
        if not value.strip():
            raise serializers.ValidationError("This field may not be blank.")
        return value

    def validate_room_sid(self, value):
        """Accept a LiveKit room sid, or nothing."""
        if not value:
            return None
        if not LIVEKIT_ROOM_SID_PATTERN.fullmatch(value):
            raise serializers.ValidationError("Must be a LiveKit room sid (RM_...).")
        return value


class ChatMessageStoredSerializer(serializers.ModelSerializer):
    """Answer to a stored chat message."""

    id = serializers.CharField(source="client_id")
    call_id = serializers.UUIDField(source="session_id")

    class Meta:
        model = ChatMessage
        fields = ["id", "call_id", "sent_at"]
        read_only_fields = fields


class CallRoomTitleSerializer(serializers.Serializer):  # pylint: disable=abstract-method
    """Validate the title given to the calls of a room."""

    title = serializers.CharField(max_length=255, allow_blank=True)


class CallListSerializer(serializers.ModelSerializer):
    """A call of the list, as annotated by `queries.with_list_data`."""

    room = serializers.SerializerMethodField()
    title = serializers.SerializerMethodField()
    is_active = serializers.BooleanField(read_only=True)
    participants = serializers.SerializerMethodField()
    participants_count = serializers.IntegerField(read_only=True)
    # whether the user is one of the participants (who may be past the first
    # ones listed), so that the frontend counts the others right
    participated = serializers.BooleanField(read_only=True)
    last_message = serializers.SerializerMethodField()
    messages_count = serializers.IntegerField(read_only=True)
    unread_count = serializers.IntegerField(read_only=True)
    has_transcript = serializers.SerializerMethodField()
    has_recording = serializers.BooleanField(read_only=True)

    class Meta:
        model = CallSession
        fields = [
            "id",
            "room",
            "title",
            "started_at",
            "ended_at",
            "is_active",
            "participants",
            "participants_count",
            "participated",
            "last_message",
            "messages_count",
            "unread_count",
            "has_transcript",
            "has_recording",
        ]
        read_only_fields = fields

    @property
    def user(self):
        """The user the call is shown to."""
        return self.context["request"].user

    def get_room(self, session):
        """Id and slug of the room, to rejoin it."""
        return {"id": str(session.room_id), "slug": session.room.slug}

    def get_title(self, session):
        """Title of the call, "" when it has none."""
        return call_title(session)

    def get_participants(self, session):
        """The first participants of the call."""
        return [
            {"name": participant.name, "is_me": participant.user_id == self.user.id}
            for participant in session.listed_participants
        ]

    def get_last_message(self, session):
        """Preview of the last message, None without messages."""
        if not session.latest_messages:
            return None
        message = session.latest_messages[0]
        return {
            "sender_name": message.sender_name,
            "text": message.text[: queries.LAST_MESSAGE_PREVIEW_LENGTH],
            "sent_at": serializers.DateTimeField().to_representation(message.sent_at),
            "is_mine": message.sender_user_id == self.user.id,
        }

    def get_has_transcript(self, session):
        """Whether the call has a transcript the user can read."""
        return bool(
            session.has_own_transcript
            or (
                session.has_any_transcript
                and session.participated
                and settings.CALLS_TRANSCRIPTS_VISIBLE_TO_PARTICIPANTS
            )
        )


class CallDetailSerializer(CallListSerializer):
    """A call with its participants, transcripts and recordings."""

    transcripts = serializers.SerializerMethodField()
    recordings = serializers.SerializerMethodField()
    can_rejoin = serializers.SerializerMethodField()

    class Meta:
        model = CallSession
        fields = [
            *CallListSerializer.Meta.fields,
            "transcripts",
            "recordings",
            "can_rejoin",
        ]
        read_only_fields = fields

    def get_participants(self, session):
        """Every participant of the call, in order of arrival."""
        datetime_field = serializers.DateTimeField()
        return [
            {
                "name": participant.name,
                "is_me": participant.user_id == self.user.id,
                "is_guest": participant.is_guest,
                "joined_at": datetime_field.to_representation(participant.joined_at),
                "left_at": (
                    datetime_field.to_representation(participant.left_at)
                    if participant.left_at
                    else None
                ),
            }
            for participant in session.listed_participants
        ]

    def get_transcripts(self, session):
        """Transcripts of the call, and whether the user can read each."""
        transcripts = list(queries.call_transcripts(session))
        own_ids = queries.own_transcript_ids(transcripts, self.user)
        datetime_field = serializers.DateTimeField()
        return [
            {
                "id": str(transcript.id),
                "status": transcript.status,
                "started_at": datetime_field.to_representation(transcript.started_at),
                "ended_at": (
                    datetime_field.to_representation(transcript.ended_at)
                    if transcript.ended_at
                    else None
                ),
                "can_view": queries.can_view_transcript(session, transcript, own_ids),
            }
            for transcript in transcripts
        ]

    def get_recordings(self, session):
        """Recordings of the call the user can access."""
        datetime_field = serializers.DateTimeField()
        return [
            {
                "id": str(recording.id),
                "status": recording.status,
                "mode": recording.mode,
                "created_at": datetime_field.to_representation(recording.created_at),
            }
            for recording in queries.call_recordings(session, self.user)
        ]

    def get_can_rejoin(self, session):
        """Whether the call is going on in a room the user can open."""
        return session.is_active and not session.room.is_deleted


class CallMessageSerializer(serializers.ModelSerializer):
    """A chat message of a call."""

    sender_key = serializers.SerializerMethodField()
    sender_is_me = serializers.SerializerMethodField()

    class Meta:
        model = ChatMessage
        fields = ["id", "sender_key", "sender_name", "sender_is_me", "text", "sent_at"]
        read_only_fields = fields

    def get_sender_key(self, message):
        """The same opaque key for the messages of one sender in a call.

        Two senders may share a name ("Гость"): the frontend groups messages
        by this key. It is a keyed hash, so the LiveKit identity is not shown
        and the key of a sender differs from one call to the next.
        """
        return salted_hmac(
            "calls.CallMessageSerializer.sender_key",
            f"{message.session_id}:{message.sender_identity}",
        ).hexdigest()[:16]

    def get_sender_is_me(self, message):
        """Whether the user sent the message."""
        return message.sender_user_id == self.context["request"].user.id
