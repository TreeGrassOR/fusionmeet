"""Record calls from LiveKit webhooks and the chat messages posted by clients.

LiveKit sends the webhooks of a room in order, on a queue per webhook URL, but
drops an event that waited more than 30 seconds: any event can be lost. The
session of a call is therefore created by whichever comes first (its
`room_started` or `participant_joined` event, or a chat message posted while
LiveKit shows the room live), and a call whose `room_finished` event was lost
is closed when the next call of the room starts, or when LiveKit shows it is
over (see `reconcile_sessions`).

Clients are not trusted: a chat message is stored only when LiveKit shows its
sender connected to the call, under the name LiveKit knows.
"""

# pylint: disable=no-member

import hashlib
import re
import uuid
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from logging import getLogger

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import F, Max, Value
from django.db.models.functions import Greatest, Least
from django.utils import timezone

from google.protobuf import json_format
from livekit import api

from core.models import Room, User

from . import room_service
from .models import CallParticipant, CallRoomTitle, CallSession, ChatMessage

logger = getLogger(__name__)

# Participants that are people: agents, egress and ingress are left out (agents
# and egress do not even keep a LiveKit room open)
HUMAN_KINDS = frozenset({api.ParticipantInfo.STANDARD, api.ParticipantInfo.SIP})

LIVEKIT_ROOM_SID_PATTERN = re.compile(r"RM_[A-Za-z0-9]+")

# Client clocks drift: a message may be dated a little before its call started
SENT_AT_TOLERANCE = timedelta(minutes=1)

# A message reaches the backend within seconds (a 5 second wait for the room
# sid, then retries after 1, 2 and 4 seconds): it is never dated earlier
SENT_AT_MAX_DELAY = timedelta(minutes=2)

# Messages retried after the end of their call are still accepted for so long
ENDED_CALL_GRACE = timedelta(minutes=2)

# How long LiveKit's word that a participant is connected to a call is trusted
PRESENCE_CACHE_SECONDS = 30

# An open call is checked against LiveKit once it is this old, and no more
# often than every RECONCILE_INTERVAL_SECONDS
RECONCILE_AFTER = timedelta(minutes=10)
RECONCILE_INTERVAL_SECONDS = 5 * 60

# Attempts to create a session that concurrent requests may create too
SESSION_CREATION_ATTEMPTS = 3


class WebhookAuthenticationError(Exception):
    """The webhook is not signed with the LiveKit API secret."""


class WebhookPayloadError(Exception):
    """The webhook body is not a LiveKit event."""


class NoLiveCallError(Exception):
    """The message names no call of the room that LiveKit shows live."""


class SenderNotConnectedError(Exception):
    """LiveKit does not show the sender connected to the call."""


class CallEndedError(Exception):
    """The call ended too long ago to accept a message."""


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
    session = CallSession.objects.select_for_update().filter(livekit_sid=sid).first()
    if session is not None:
        if started_at < session.started_at:
            # Opened by an event that did not tell when LiveKit created the room
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
        logger.info(
            "Closing call %s of room %s: its room_finished event was lost",
            open_session.pk,
            room.pk,
        )
        close_session(open_session, estimate_end(open_session, started_at))

    session = CallSession.objects.create(
        room=room,
        livekit_sid=sid,
        title=get_room_call_title(room),
        started_at=started_at,
        last_activity_at=started_at,
    )
    logger.info("Call %s started in room %s (sid %s)", session.pk, room.pk, sid)
    return session


def ensure_session(room, sid, started_at):
    """Return the session of a LiveKit room instance, created if needed.

    The sid must come from LiveKit. A room has at most one open session: an
    open session with another sid is closed, since its `room_finished` event
    was lost.
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


def live_room_started_at(live_room):
    """Time at which LiveKit created a live room instance."""
    return (
        from_milliseconds(live_room.creation_time_ms)
        or from_seconds(live_room.creation_time)
        or timezone.now()
    )


def reconcile_sessions(sessions):
    """Close the open calls among some sessions that LiveKit shows are over.

    It catches the calls whose `room_finished` event was lost in a room that
    is not used again. Only calls open for a while are checked, each at most
    every few minutes; nothing is done when LiveKit does not answer.

    Returns:
        The sessions closed.
    """
    now = timezone.now()
    candidates = [
        session
        for session in sessions
        if session.ended_at is None and session.started_at < now - RECONCILE_AFTER
    ]
    candidates = [
        session
        for session in candidates
        if cache.add(f"calls:reconcile:{session.pk}", True, RECONCILE_INTERVAL_SECONDS)
    ]
    if not candidates:
        return []

    try:
        live_rooms = room_service.get_live_rooms(
            session.room_id for session in candidates
        )
    except room_service.LiveKitUnavailableError as error:
        logger.warning("Could not check the open calls with LiveKit: %s", error)
        return []

    closed = []
    for session in candidates:
        live_room = live_rooms.get(str(session.room_id))
        if live_room is not None and live_room.sid == session.livekit_sid:
            continue
        next_started_at = live_room_started_at(live_room) if live_room else now
        logger.info(
            "Closing call %s of room %s: LiveKit shows it is over",
            session.pk,
            session.room_id,
        )
        close_session(session, estimate_end(session, next_started_at))
        closed.append(session)
    return closed


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

    def _handle_room_finished(self, room, event):  # pylint: disable=unused-argument
        """Close the call (also in a deleted room)."""
        session = CallSession.objects.filter(livekit_sid=event.room.sid).first()
        if session is None:
            return
        close_session(session, self._event_time(event))


def _presence_cache_key(session, identity):
    """Cache key of LiveKit's word that an identity is connected to a call."""
    digest = hashlib.sha256(identity.encode()).hexdigest()
    return f"calls:presence:{session.pk}:{digest}"


def verify_sender(room, session, identity, live_room=None):
    """Check with LiveKit that a sender is connected to an open call.

    The sender is recorded as a participant of the call from LiveKit's answer,
    as its `participant_joined` event would, and LiveKit's answer is trusted
    for PRESENCE_CACHE_SECONDS. `live_room` is the LiveKit room of the call
    when it was just asked.

    Returns:
        The name of the sender in the call (renames included).

    Raises:
        NoLiveCallError: LiveKit does not show the call live.
        SenderNotConnectedError: LiveKit does not show the sender connected.
        room_service.LiveKitUnavailableError: LiveKit did not answer.
    """
    key = _presence_cache_key(session, identity)
    cached = cache.get(key)
    if cached is not None:
        return cached["name"]

    if live_room is None:
        live_room = room_service.get_live_room(room.id)
        if live_room is None or live_room.sid != session.livekit_sid:
            raise NoLiveCallError(session.livekit_sid)

    info = room_service.get_participant(room.id, identity)
    if (
        info is None
        or info.kind not in HUMAN_KINDS
        or info.state == api.ParticipantInfo.DISCONNECTED
    ):
        raise SenderNotConnectedError(identity)

    name = info.name[:255]
    record_join(
        session,
        identity=identity,
        name=name,
        user=resolve_user(identity, info.attributes.get("is_authenticated") == "true"),
        joined_at=from_milliseconds(info.joined_at_ms)
        or from_seconds(info.joined_at)
        or timezone.now(),
    )
    cache.set(key, {"name": name}, PRESENCE_CACHE_SECONDS)
    return name


def find_message_session(room, room_sid):
    """Return the call a chat message belongs to, with its LiveKit room when
    LiveKit was asked.

    The client sends the sid of the room instance it is connected to. A
    recorded call of the room with that sid is the one. Otherwise (no sid, or
    the webhooks of the call did not arrive yet), it is the call LiveKit shows
    live, recorded here if needed: a client never opens a call that LiveKit
    does not show, and never files a message into another call.

    Raises:
        NoLiveCallError: no call of the room is live, or not the one of the sid.
        room_service.LiveKitUnavailableError: LiveKit did not answer.
    """
    if room_sid:
        session = CallSession.objects.filter(room=room, livekit_sid=room_sid).first()
        if session is not None:
            return session, None

    live_room = room_service.get_live_room(room.id)
    if live_room is None or (room_sid and room_sid != live_room.sid):
        raise NoLiveCallError(room_sid)
    session = ensure_session(room, live_room.sid, live_room_started_at(live_room))
    return session, live_room


def store_chat_message(  # noqa: PLR0913  # pylint: disable=too-many-arguments
    room, *, identity, client_id, text, sent_at, room_sid
):
    """Store a chat message posted by its sender's client.

    The message id is the LiveKit text stream id, which every participant
    receives: it identifies a message of its sender, and posting it again is a
    no-op. The sender must be connected to the call, or have been a
    participant of a call that just ended (a message retried after the end).
    The client's clock is only trusted within a short delay before receipt.

    Returns:
        A (message, created) tuple.

    Raises:
        CallEndedError: the call ended more than ENDED_CALL_GRACE ago.
        NoLiveCallError: the message names no live call of the room.
        SenderNotConnectedError: the sender is not connected to the call.
        room_service.LiveKitUnavailableError: LiveKit did not answer.
    """
    existing = ChatMessage.objects.filter(
        session__room=room, sender_identity=identity, client_id=client_id
    ).first()
    if existing is not None:
        return existing, False

    received_at = timezone.now()
    session, live_room = find_message_session(room, room_sid)
    if session.ended_at is None:
        name = verify_sender(room, session, identity, live_room)
        latest = received_at
    else:
        if received_at > session.ended_at + ENDED_CALL_GRACE:
            raise CallEndedError(session.pk)
        name = ""
        latest = min(received_at, session.ended_at)

    # An ended call gets no new participant: only its participants may post
    participant = CallParticipant.objects.filter(
        session=session, identity=identity
    ).first()
    if participant is None:
        raise SenderNotConnectedError(identity)

    earliest = max(
        session.started_at - SENT_AT_TOLERANCE, received_at - SENT_AT_MAX_DELAY
    )
    sent_at = min(max(sent_at, earliest), latest)

    try:
        with transaction.atomic():
            message = ChatMessage.objects.create(
                session=session,
                client_id=client_id,
                sender_identity=identity,
                sender_user=participant.user,
                sender_name=name or participant.name,
                text=text,
                sent_at=sent_at,
            )
    except (DjangoValidationError, IntegrityError):
        # Posted twice at the same time
        existing = ChatMessage.objects.filter(
            session=session, sender_identity=identity, client_id=client_id
        ).first()
        if existing is None:
            raise
        return existing, False

    # Like sent_at, never past the end of the call
    CallSession.objects.filter(pk=session.pk).update(
        last_activity_at=Greatest(F("last_activity_at"), Value(message.sent_at))
    )
    return message, True
