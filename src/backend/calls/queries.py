"""Querysets of the calls a user can see, with the data shown in the call list.

Everything the list shows is annotated or prefetched, so that a page of calls
takes a fixed number of queries.
"""

# pylint: disable=no-member

from datetime import datetime, timedelta
from datetime import timezone as dt_timezone

from django.conf import settings
from django.db.models import (
    Count,
    DateTimeField,
    Exists,
    ExpressionWrapper,
    F,
    Max,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Value,
)
from django.db.models.functions import Coalesce, Greatest
from django.utils import timezone

from core.models import (
    Recording,
    RecordingStatusChoices,
    ResourceAccess,
    RoleChoices,
)

from transcripts.models import Transcript, TranscriptStatusChoices

from .models import CallParticipant, CallSession, CallUserState, ChatMessage

# Transcripts and recordings belong to the call of their room that was live when
# they started; the tolerance absorbs a call start known only approximately
LINK_TOLERANCE = timedelta(seconds=30)

# Participants listed with each call of the list (the count gives the others)
LISTED_PARTICIPANTS = 6

# Length of the last message shown in the call list
LAST_MESSAGE_PREVIEW_LENGTH = 200

EPOCH = datetime(1970, 1, 1, tzinfo=dt_timezone.utc)

FAILED_RECORDING_STATUSES = [
    RecordingStatusChoices.FAILED,
    RecordingStatusChoices.FAILED_TO_START,
]


def _count(model):
    """Number of rows of a model linked to the outer call."""
    return Coalesce(
        Subquery(
            model.objects.filter(session=OuterRef("pk"))
            .order_by()
            .values("session")
            .annotate(count=Count("pk"))
            .values("count")[:1]
        ),
        0,
    )


def visible_calls(user):
    """Calls a user can see: the ones they attended, and the ones of the rooms
    they own or administrate when CALLS_VISIBLE_TO_ROOM_ADMINS is set.

    Calls nobody joined and nobody wrote in are left out.
    """
    visibility = Q(
        Exists(CallParticipant.objects.filter(session=OuterRef("pk"), user=user))
    )
    if settings.CALLS_VISIBLE_TO_ROOM_ADMINS:
        visibility |= Q(
            Exists(
                ResourceAccess.objects.filter(
                    resource=OuterRef("room_id"),
                    user=user,
                    role__in=[RoleChoices.OWNER, RoleChoices.ADMIN],
                )
            )
        )

    has_content = Q(Exists(CallParticipant.objects.filter(session=OuterRef("pk")))) | Q(
        Exists(ChatMessage.objects.filter(session=OuterRef("pk")))
    )

    return CallSession.objects.filter(visibility, has_content).annotate(
        participated=Exists(
            CallParticipant.objects.filter(session=OuterRef("pk"), user=user)
        ),
        link_from=ExpressionWrapper(
            F("started_at") - LINK_TOLERANCE, output_field=DateTimeField()
        ),
        link_until=Coalesce(F("ended_at"), Value(timezone.now())),
    )


def search_calls(queryset, query):
    """Calls whose title, room, participants or messages contain a text."""
    return queryset.filter(
        Q(title__icontains=query)
        | Q(room__name__icontains=query)
        | Q(room__call_title__title__icontains=query)
        | Q(
            Exists(
                CallParticipant.objects.filter(
                    session=OuterRef("pk"), name__icontains=query
                )
            )
        )
        | Q(
            Exists(
                ChatMessage.objects.filter(
                    session=OuterRef("pk"), text__icontains=query
                )
            )
        )
    )


def transcripts_of_calls():
    """Transcripts started in the room of the outer call, while it was live."""
    return Transcript.objects.filter(
        room=OuterRef("room_id"),
        started_at__gte=OuterRef("link_from"),
        started_at__lte=OuterRef("link_until"),
    )


def recordings_of_calls(user):
    """Recordings made in the room of the outer call that a user can access."""
    return Recording.objects.filter(
        Q(accesses__user=user) | Q(accesses__team__in=user.get_teams()),
        room=OuterRef("room_id"),
        created_at__gte=OuterRef("link_from"),
        created_at__lte=OuterRef("link_until"),
    )


def with_list_data(queryset, user, *, all_participants=False):
    """Annotate and prefetch what the call list shows for a user.

    The list shows the first participants of each call; the detail of a call
    (`all_participants`) shows all of them.
    """
    participants = CallParticipant.objects.order_by("joined_at", "id")
    if not all_participants:
        participants = participants[:LISTED_PARTICIPANTS]
    now = timezone.now()
    my_presence = (
        CallParticipant.objects.filter(session=OuterRef("pk"), user=user)
        .order_by()
        .values("session")
        .annotate(until=Max(Coalesce("left_at", Value(now))))
        .values("until")[:1]
    )
    last_read = CallUserState.objects.filter(session=OuterRef("pk"), user=user).values(
        "last_read_at"
    )[:1]

    return (
        queryset.select_related("room", "room__call_title")
        .annotate(
            participants_count=_count(CallParticipant),
            messages_count=_count(ChatMessage),
            # Messages sent while the user was in the call, or before they last
            # opened it, are read
            unread_since=Coalesce(
                Greatest(Subquery(last_read), Subquery(my_presence)),
                Value(EPOCH),
            ),
            has_any_transcript=Exists(
                transcripts_of_calls().exclude(status=TranscriptStatusChoices.FAILED)
            ),
            has_own_transcript=Exists(
                transcripts_of_calls()
                .exclude(status=TranscriptStatusChoices.FAILED)
                .filter(users=user)
            ),
            has_recording=Exists(
                recordings_of_calls(user).exclude(status__in=FAILED_RECORDING_STATUSES)
            ),
        )
        .annotate(
            unread_count=Coalesce(
                Subquery(
                    ChatMessage.objects.filter(
                        session=OuterRef("pk"), sent_at__gt=OuterRef("unread_since")
                    )
                    .exclude(sender_user=user)
                    .order_by()
                    .values("session")
                    .annotate(count=Count("pk"))
                    .values("count")[:1]
                ),
                0,
            )
        )
        .prefetch_related(
            Prefetch(
                "participants", queryset=participants, to_attr="listed_participants"
            ),
            Prefetch(
                "messages",
                queryset=ChatMessage.objects.order_by("-sent_at", "-created_at")[:1],
                to_attr="latest_messages",
            ),
        )
        .order_by("-last_activity_at", "-started_at", "-id")
    )


def can_view_transcript(session, transcript, own_ids) -> bool:
    """Whether a user can read a transcript of a call they can see.

    `own_ids` are the ids of the transcripts the user was given access to.
    """
    if transcript.id in own_ids:
        return True
    return bool(
        settings.CALLS_TRANSCRIPTS_VISIBLE_TO_PARTICIPANTS and session.participated
    )


def call_transcripts(session):
    """Transcripts of a call (annotated by `visible_calls`), oldest first."""
    return Transcript.objects.filter(
        room_id=session.room_id,
        started_at__gte=session.link_from,
        started_at__lte=session.link_until,
    ).order_by("started_at", "id")


def own_transcript_ids(transcripts, user):
    """Ids of the transcripts a user was given access to."""
    return set(
        Transcript.users.through.objects.filter(
            transcript__in=transcripts, user=user
        ).values_list("transcript_id", flat=True)
    )


def call_recordings(session, user):
    """Recordings of a call (annotated by `visible_calls`) a user can access."""
    return (
        Recording.objects.filter(
            Q(accesses__user=user) | Q(accesses__team__in=user.get_teams()),
            room_id=session.room_id,
            created_at__gte=session.link_from,
            created_at__lte=session.link_until,
        )
        .distinct()
        .order_by("created_at", "id")
    )
