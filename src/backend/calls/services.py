"""Record calls from LiveKit webhooks and the chat messages posted by clients.

LiveKit sends the webhooks of a room in order, on a queue per webhook URL, but
drops an event that waited more than 30 seconds: any event can be lost. The
session of a call is therefore created by whichever comes first (its
`room_started` or `participant_joined` event, or a chat message), and a call
whose `room_finished` event was lost is closed when the next call of the room
starts.
"""

# pylint: disable=no-member

import re
import uuid
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from logging import getLogger

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Max, Value
from django.db.models.functions import Greatest, Least
from django.utils import timezone

from google.protobuf import json_format
from livekit import api

from core.models import Room, User

from .models import CallParticipant, CallRoomTitle, CallSession, ChatMessage

logger = getLogger(__name__)

# Participants that are people: agents, egress and ingress are left out (agents
# and egress do not even keep a LiveKit room open)
HUMAN_KINDS = frozenset({api.ParticipantInfo.STANDARD, api.ParticipantInfo.SIP})

LIVEKIT_ROOM_SID_PATTERN = re.compile(r"RM_[A-Za-z0-9]+")

# Client clocks drift: a message may be dated a little before its call started
SENT_AT_TOLERANCE = timedelta(minutes=1)

# Attempts to create a session that concurrent requests may create too
SESSION_CREATION_ATTEMPTS = 3


class WebhookAuthenticationError(Exception):
    """The webhook is not signed with the LiveKit API secret."""


class WebhookPayloadError(Exception):
    """The webhook body is not a LiveKit event."""


class ChatMessageConflictError(Exception):
    """A message with the same id was posted by another participant."""


def from_milliseconds(value):
    """Convert epoch milliseconds into an aware datetime, None for 0."""
    if not value:
        return None
    seconds, milliseconds = divmod(value, 1000)
    return datetime.fromtimestamp(seconds, tz=dt_timezone.utc) + timedelta(
        milliseconds=milliseconds
    )


def from_seconds(value):
    """Convert epoch seconds into an aware datetime, None for 0."""
    if not value:
        return None
    return datetime.fromtimestamp(value, tz=dt_timezone.utc)


def get_room_call_title(room) -> str:
    """Return the title given to the calls of a room, "" when none."""
    return (
        CallRoomTitle.objects.filter(room=room).values_list("title", flat=True).first()
        or ""
    )


def estimate_end(session, next_started_at):
    """Estimate when a call whose `room_finished` event was lost ended.

    It is the last trace of activity (a join, a departure or a chat message),
    never after the start of the next call.
    """
    traces = session.participants.aggregate(
        left=Max("left_at"), joined=Max("last_joined_at")
    )
    candidates = [session.last_activity_at, traces["left"], traces["joined"]]
    last_trace = max(moment for moment in candidates if moment is not None)
    return max(session.started_at, min(last_trace, next_started_at))


def close_session(session, ended_at):
    """End a call, and every participant still present in it.

    A call that no person joined (LiveKit creates a room for a connection
    that may never succeed) is deleted.
    """
    updated = CallSession.objects.filter(pk=session.pk, ended_at__isnull=True).update(
        ended_at=Greatest(F("started_at"), Value(ended_at))
    )
    if not updated:
        return

    CallParticipant.objects.filter(session=session, left_at__isnull=True).update(
        left_at=Greatest(F("last_joined_at"), Value(ended_at))
    )

    if (
        not CallParticipant.objects.filter(session=session).exists()
        and not ChatMessage.objects.filter(session=session).exists()
    ):
        CallSession.objects.filter(pk=session.pk).delete()
        logger.info("Deleted call %s: nobody joined it", session.pk)
    else:
        logger.info("Call %s ended", session.pk)


def _get_or_create_session(room, sid, started_at):
    """Find or create the session of a room instance (see `ensure_session`)."""
    if sid:
        session = (
            CallSession.objects.select_for_update().filter(livekit_sid=sid).first()
        )
        if session is not None:
            if started_at < session.started_at:
                # Created by a chat message, before the webhooks arrived
                CallSession.objects.filter(pk=session.pk).update(
                    started_at=Least(F("started_at"), Value(started_at))
                )
                session.refresh_from_db()
            return session

    open_session = (
        CallSession.objects.select_for_update()
        .filter(room=room, ended_at__isnull=True)
        .first()
    )
    if open_session is not None:
        if not sid:
            return open_session

        if open_session.livekit_sid is None:
            # Created by a chat message posted without the room sid
            CallSession.objects.filter(pk=open_session.pk).update(
                livekit_sid=sid, started_at=Least(F("started_at"), Value(started_at))
            )
            open_session.refresh_from_db()
            return open_session

        logger.info(
            "Closing call %s of room %s: its room_finished event was lost",
            open_session.pk,
            room.pk,
        )
        close_session(open_session, estimate_end(open_session, started_at))

    session = CallSession.objects.create(
        room=room,
        livekit_sid=sid or None,
        title=get_room_call_title(room),
        started_at=started_at,
        last_activity_at=started_at,
    )
    logger.info("Call %s started in room %s (sid %s)", session.pk, room.pk, sid)
    return session


def ensure_session(room, sid, started_at):
    """Return the session of a LiveKit room instance, created if needed.

    The sid must come from LiveKit. Without a sid, the open session of the room
    is returned (or a new one, that a later webhook completes with its sid). A
    room has at most one open session: an open session with another sid is
    closed, since its `room_finished` event was lost.
    """
    last_error = None
    for _attempt in range(SESSION_CREATION_ATTEMPTS):
        try:
            with transaction.atomic():
                return _get_or_create_session(room, sid, started_at)
        except (DjangoValidationError, IntegrityError) as error:
            # A concurrent webhook or message created the session first
            last_error = error
    raise last_error


def resolve_user(identity, is_authenticated):
    """Return the user behind a LiveKit identity, None for guests.

    Authenticated participants have their user's `sub` as identity; guests have
    a random one, never linked to an account.
    """
    if not is_authenticated or not identity:
        return None
    return User.objects.filter(sub=identity).first()


def record_join(session, *, identity, name, user, joined_at):
    """Record that a participant joined (or joined again) a call."""
    with transaction.atomic():
        participant = (
            CallParticipant.objects.select_for_update()
            .filter(session=session, identity=identity)
            .first()
        )
        if participant is None:
            try:
                with transaction.atomic():
                    return CallParticipant.objects.create(
                        session=session,
                        identity=identity,
                        user=user,
                        name=name,
                        is_guest=user is None,
                        joined_at=joined_at,
                        last_joined_at=joined_at,
                    )
            except (DjangoValidationError, IntegrityError):
                participant = CallParticipant.objects.select_for_update().get(
                    session=session, identity=identity
                )

        changes = {}
        if name and name != participant.name:
            changes["name"] = name
        if user is not None and participant.user_id is None:
            changes.update(user=user, is_guest=False)
        if joined_at < participant.joined_at:
            changes["joined_at"] = joined_at
        if joined_at > participant.last_joined_at:
            changes["last_joined_at"] = joined_at
            if participant.left_at is not None and joined_at >= participant.left_at:
                # Back in the call
                changes["left_at"] = None
        if changes:
            CallParticipant.objects.filter(pk=participant.pk).update(**changes)
        return participant


def record_leave(sid, *, identity, connection_joined_at, left_at):
    """Record that a participant left a call.

    The departure of a connection older than the participant's latest one is
    ignored: LiveKit disconnects the previous connection of an identity that
    joins again (e.g. from another tab), possibly after the new one joined.
    """
    participants = CallParticipant.objects.filter(
        session__livekit_sid=sid, identity=identity
    )
    if connection_joined_at is not None:
        participants = participants.filter(last_joined_at__lte=connection_joined_at)
    participants.update(
        left_at=Greatest(F("left_at"), Value(left_at), F("last_joined_at"))
    )


class CallWebhookService:
    """Process the LiveKit webhooks sent to the calls application.

    The signature is verified as `core.services.livekit_events` does it. Events
    of other rooms (connection tests, rooms filtered out, unknown or deleted
    rooms) and of non-human participants are ignored.
    """

    def __init__(self):
        token_verifier = api.TokenVerifier(
            settings.LIVEKIT_CONFIGURATION["api_key"],
            settings.LIVEKIT_CONFIGURATION["api_secret"],
        )
        self.webhook_receiver = api.WebhookReceiver(token_verifier)
        self._handlers = {
            "room_started": self._handle_room_started,
            "participant_joined": self._handle_participant_joined,
            "participant_left": self._handle_participant_left,
            "participant_connection_aborted": self._handle_participant_left,
            "room_finished": self._handle_room_finished,
        }

        self._filter_regex = None
        if settings.LIVEKIT_WEBHOOK_EVENTS_FILTER_REGEX:
            try:
                self._filter_regex = re.compile(
                    settings.LIVEKIT_WEBHOOK_EVENTS_FILTER_REGEX
                )
            except re.error:
                logger.exception(
                    "Invalid LIVEKIT_WEBHOOK_EVENTS_FILTER_REGEX. Webhook filtering disabled."
                )

    def receive(self, request):
        """Verify a webhook request, then process its event.

        Raises:
            WebhookAuthenticationError: missing or invalid signature.
            WebhookPayloadError: the signed body is not a LiveKit event.
        """
        auth_token = request.headers.get("Authorization")
        if not auth_token:
            raise WebhookAuthenticationError("Authorization header missing")

        # LiveKit sends UTF-8: another body cannot match the signature
        body = request.body.decode("utf-8", errors="replace")

        try:
            event = self.webhook_receiver.receive(body, auth_token)
        except json_format.ParseError as e:
            raise WebhookPayloadError("Invalid webhook payload") from e
        except Exception as e:
            raise WebhookAuthenticationError("Invalid webhook signature") from e

        self.handle(event)

    def handle(self, event):
        """Process a verified LiveKit event."""
        handler = self._handlers.get(event.event)
        if handler is None or not event.room.sid:
            return

        room = self._get_room(event.room.name)
        if room is None:
            return

        handler(room, event)

    def _get_room(self, room_name):
        """Return the room of a LiveKit room name, None when it is not ours."""
        if not room_name or room_name.startswith(settings.CONNECTION_TEST_ROOM_PREFIX):
            return None

        if self._filter_regex and not self._filter_regex.search(room_name):
            return None

        try:
            room_id = uuid.UUID(room_name)
        except ValueError:
            logger.info("Ignoring webhook event for room '%s'", room_name)
            return None

        room = Room.all_objects.filter(pk=room_id).first()
        if room is None:
            logger.info("Ignoring webhook event for unknown room %s", room_id)
        return room

    @staticmethod
    def _event_time(event):
        """Time at which LiveKit emitted an event."""
        return from_seconds(event.created_at) or timezone.now()

    def _room_started_at(self, event):
        """Time at which LiveKit created the room instance of an event."""
        return (
            from_milliseconds(event.room.creation_time_ms)
            or from_seconds(event.room.creation_time)
            or self._event_time(event)
        )

    def _handle_room_started(self, room, event):
        """Open the call."""
        if room.is_deleted:
            return
        ensure_session(room, event.room.sid, self._room_started_at(event))

    def _handle_participant_joined(self, room, event):
        """Add a person to the call, opening it if its start was missed."""
        participant = event.participant
        if room.is_deleted or participant.kind not in HUMAN_KINDS:
            return
        if not participant.identity:
            return

        session = ensure_session(room, event.room.sid, self._room_started_at(event))
        record_join(
            session,
            identity=participant.identity,
            name=participant.name[:255],
            user=resolve_user(
                participant.identity,
                participant.attributes.get("is_authenticated") == "true",
            ),
            joined_at=from_milliseconds(participant.joined_at_ms)
            or self._event_time(event),
        )

    def _handle_participant_left(self, room, event):  # pylint: disable=unused-argument
        """Record the departure of a person (also from a deleted room)."""
        participant = event.participant
        if participant.kind not in HUMAN_KINDS or not participant.identity:
            return

        record_leave(
            event.room.sid,
            identity=participant.identity,
            connection_joined_at=from_milliseconds(participant.joined_at_ms),
            left_at=self._event_time(event),
        )

    def _handle_room_finished(self, room, event):
        """Close the call (also in a deleted room)."""
        session = CallSession.objects.filter(livekit_sid=event.room.sid).first()
        if session is None:
            # Its room_started and participant_joined events were lost: only
            # chat messages, posted without the sid, opened it
            session = CallSession.objects.filter(
                room=room, ended_at__isnull=True, livekit_sid__isnull=True
            ).first()
        if session is None:
            return

        if session.livekit_sid is None:
            CallSession.objects.filter(pk=session.pk).update(livekit_sid=event.room.sid)
        close_session(session, self._event_time(event))


def display_name_of(request, name):
    """Name under which a chat message is stored.

    The name chosen in the call when the participant can edit it (guests, or
    everyone with AUTHENTICATED_PARTICIPANTS_CAN_EDIT_DISPLAY_NAME), otherwise
    the name of their LiveKit token.
    """
    can_edit = (
        settings.AUTHENTICATED_PARTICIPANTS_CAN_EDIT_DISPLAY_NAME
        or not request.user.is_authenticated
    )
    chosen = (name or "").strip() if can_edit else ""
    return (chosen or request.auth.name or "")[:255]


def sender_user_of(request):
    """User who posts a chat message with a LiveKit token, None for guests."""
    attributes = request.auth.attributes or {}
    if request.user.is_authenticated and attributes.get("is_authenticated") == "true":
        return request.user
    return None


def record_sender(session, *, identity, user, name, at):
    """Make sure the sender of a chat message is a participant of its call.

    Without its `participant_joined` event, the participant is recorded as
    joined when the message was sent. In-call renames have no webhook: the
    latest name is the one of the latest message.
    """
    participant = CallParticipant.objects.filter(
        session=session, identity=identity
    ).first()
    if participant is None:
        try:
            with transaction.atomic():
                CallParticipant.objects.create(
                    session=session,
                    identity=identity,
                    user=user,
                    name=name,
                    is_guest=user is None,
                    joined_at=at,
                    # Any departure of this connection must be recorded
                    last_joined_at=min(at, session.started_at),
                )
        except (DjangoValidationError, IntegrityError):
            pass  # Its participant_joined event was processed meanwhile
        return

    if name and name != participant.name:
        CallParticipant.objects.filter(pk=participant.pk).update(name=name)


def get_message_session(room, room_sid, sent_at):
    """Return the call a chat message belongs to.

    The room sid sent by the client only selects a recorded call of the room:
    a client never closes a call or writes a sid, which come from LiveKit
    webhooks only. Without such a call, the message goes to the open call of
    the room, or opens one that the webhooks of the call complete.
    """
    if room_sid:
        session = CallSession.objects.filter(room=room, livekit_sid=room_sid).first()
        if session is not None:
            return session
    return ensure_session(room, None, sent_at)


def store_chat_message(  # noqa: PLR0913  # pylint: disable=too-many-arguments
    room, *, identity, user, name, client_id, text, sent_at, room_sid
):
    """Store a chat message posted by its sender's client.

    The message id is the LiveKit text stream id: posting it again is a no-op.

    Returns:
        A (message, created) tuple.

    Raises:
        ChatMessageConflictError: another participant posted a message with
            the same id.
    """
    existing = ChatMessage.objects.filter(
        session__room=room, client_id=client_id
    ).first()
    if existing is not None:
        if existing.sender_identity != identity:
            raise ChatMessageConflictError(client_id)
        return existing, False

    now = timezone.now()
    session = get_message_session(
        room, room_sid, min(max(sent_at, now - SENT_AT_TOLERANCE), now)
    )
    sent_at = min(max(sent_at, session.started_at - SENT_AT_TOLERANCE), now)

    record_sender(session, identity=identity, user=user, name=name, at=sent_at)

    try:
        with transaction.atomic():
            message = ChatMessage.objects.create(
                session=session,
                client_id=client_id,
                sender_identity=identity,
                sender_user=user,
                sender_name=name,
                text=text,
                sent_at=sent_at,
            )
    except (DjangoValidationError, IntegrityError):
        # Posted twice at the same time
        existing = ChatMessage.objects.filter(
            session=session, client_id=client_id
        ).first()
        if existing is None:
            raise
        if existing.sender_identity != identity:
            raise ChatMessageConflictError(client_id) from None
        return existing, False

    CallSession.objects.filter(pk=session.pk).update(
        last_activity_at=Greatest(F("last_activity_at"), Value(message.sent_at))
    )
    return message, True
