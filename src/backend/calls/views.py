"""API endpoints of the calls application."""

import json
import uuid
from logging import getLogger

from django.conf import settings
from django.core.files.storage import default_storage
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone

from botocore.exceptions import BotoCoreError, ClientError
from lasuite.drf.throttling import MonitoredThrottleMixin
from rest_framework import (
    decorators,
    generics,
    mixins,
    throttling,
    views,
    viewsets,
)
from rest_framework import (
    exceptions as drf_exceptions,
)
from rest_framework import (
    permissions as drf_permissions,
)
from rest_framework import (
    response as drf_response,
)
from rest_framework import (
    status as drf_status,
)

from core.api.exceptions import ensure_room_not_deleted
from core.api.permissions import HasLiveKitRoomAccess
from core.api.viewsets import Pagination
from core.authentication.livekit import LiveKitTokenAuthentication
from core.models import Room

from transcripts.models import TranscriptFormat

from . import queries, serializers, services
from .models import CallRoomTitle, CallUserState, ChatMessage

logger = getLogger(__name__)

# Longest search text in the call list
MAX_SEARCH_LENGTH = 100


class CallsHistoryEnabledMixin:
    """Hide an endpoint when the call history is disabled."""

    def initial(self, request, *args, **kwargs):
        """Answer 404 when CALLS_HISTORY_ENABLED is not set."""
        if not settings.CALLS_HISTORY_ENABLED:
            raise Http404
        super().initial(request, *args, **kwargs)


class ChatMessageRateThrottle(MonitoredThrottleMixin, throttling.SimpleRateThrottle):
    """Limit the chat messages a participant posts, whatever the room."""

    scope = "call_chat_messages"
    rate = "120/minute"

    def get_cache_key(self, request, view):
        """Throttle per LiveKit identity."""
        identity = getattr(request.auth, "identity", None)
        if not identity:
            return None
        return self.cache_format % {"scope": self.scope, "ident": identity}


class CallsLiveKitWebhookView(CallsHistoryEnabledMixin, views.APIView):
    """Receive the LiveKit webhooks that open and close calls.

    LiveKit sends every webhook to each configured URL: this one is set next to
    the core endpoint (rooms/webhooks-livekit/), which it does not depend on.
    Ignored events are acknowledged with a 200, so that LiveKit does not retry
    them; database errors give a 500, which LiveKit retries.
    """

    authentication_classes = []
    permission_classes = []

    def post(self, request):
        """Process one webhook."""
        try:
            services.CallWebhookService().receive(request)
        except services.WebhookAuthenticationError:
            return drf_response.Response(
                {"status": "error"}, status=drf_status.HTTP_401_UNAUTHORIZED
            )
        except services.WebhookPayloadError:
            return drf_response.Response(
                {"status": "error"}, status=drf_status.HTTP_400_BAD_REQUEST
            )
        return drf_response.Response({"status": "success"})


class RoomChatMessagesView(CallsHistoryEnabledMixin, generics.GenericAPIView):
    """Store the chat messages a participant sends in a call.

    The sender's client posts each message it sent, with its LiveKit token:
    a participant can only post as itself, in the room of its token. Guests
    post too.
    """

    authentication_classes = [LiveKitTokenAuthentication]
    permission_classes = [HasLiveKitRoomAccess]
    throttle_classes = [ChatMessageRateThrottle]
    serializer_class = serializers.ChatMessageCreateSerializer

    def post(self, request, pk):
        """Store one message: 201 when stored, 200 when already stored."""
        room = get_object_or_404(Room.all_objects, pk=pk)
        self.check_object_permissions(request, room)
        ensure_room_not_deleted(room)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            message, created = services.store_chat_message(
                room,
                identity=request.auth.identity,
                user=services.sender_user_of(request),
                name=services.display_name_of(request, data.get("name")),
                client_id=data["id"],
                text=data["text"],
                sent_at=services.from_milliseconds(data["sent_at"]) or timezone.now(),
                room_sid=data.get("room_sid"),
            )
        except services.ChatMessageConflictError:
            return drf_response.Response(
                {"error": "A message with this id was sent by another participant."},
                status=drf_status.HTTP_409_CONFLICT,
            )

        return drf_response.Response(
            serializers.ChatMessageStoredSerializer(message).data,
            status=drf_status.HTTP_201_CREATED if created else drf_status.HTTP_200_OK,
        )


class RoomCallTitleView(CallsHistoryEnabledMixin, generics.GenericAPIView):
    """Give a title to the next calls of a room (e.g. a scheduled meeting).

    Reserved to the room owners and administrators. An empty title removes it.
    """

    permission_classes = [drf_permissions.IsAuthenticated]
    serializer_class = serializers.CallRoomTitleSerializer

    def put(self, request, pk):
        """Set or clear the title."""
        room = get_object_or_404(Room.all_objects, pk=pk)
        if not room.is_administrator_or_owner(request.user):
            raise drf_exceptions.PermissionDenied()
        ensure_room_not_deleted(room)

        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        title = serializer.validated_data["title"]

        if title:
            CallRoomTitle.objects.update_or_create(
                room=room, defaults={"title": title, "updated_by": request.user}
            )
        else:
            CallRoomTitle.objects.filter(room=room).delete()

        return drf_response.Response({"title": title})


class CallViewSet(
    CallsHistoryEnabledMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """The calls of the logged-in user, with their chat and transcripts."""

    pagination_class = Pagination
    permission_classes = [drf_permissions.IsAuthenticated]
    serializer_class = serializers.CallListSerializer
    lookup_value_regex = "[0-9a-fA-F-]{36}"

    def get_queryset(self):
        """Calls the user can see, with the data shown for the action."""
        user = self.request.user
        queryset = queries.visible_calls(user)

        if self.action == "list":
            search = self.request.query_params.get("q", "").strip()
            if search:
                queryset = queries.search_calls(queryset, search[:MAX_SEARCH_LENGTH])
            return queries.with_list_data(queryset, user)

        if self.action == "retrieve":
            return queries.with_list_data(queryset, user, all_participants=True)

        return queryset.select_related("room")

    def get_serializer_class(self):
        """The detail shows more than the list."""
        if self.action == "retrieve":
            return serializers.CallDetailSerializer
        return super().get_serializer_class()

    @decorators.action(detail=True, methods=["get"])
    def messages(self, request, pk=None):  # pylint: disable=unused-argument
        """Chat messages of a call, oldest first."""
        session = self.get_object()
        page = self.paginate_queryset(
            ChatMessage.objects.filter(session=session).order_by(
                "sent_at", "created_at", "id"
            )
        )
        serializer = serializers.CallMessageSerializer(
            page, many=True, context=self.get_serializer_context()
        )
        return self.get_paginated_response(serializer.data)

    @decorators.action(detail=True, methods=["post"])
    def read(self, request, pk=None):  # pylint: disable=unused-argument
        """Mark the messages of a call as read."""
        session = self.get_object()
        CallUserState.objects.update_or_create(
            session=session,
            user=request.user,
            defaults={"last_read_at": timezone.now()},
        )
        return drf_response.Response(status=drf_status.HTTP_204_NO_CONTENT)

    @decorators.action(
        detail=True,
        methods=["get"],
        url_path=r"transcripts/(?P<transcript_id>[0-9a-fA-F-]{36})",
    )
    def transcript(self, request, pk=None, transcript_id=None):  # pylint: disable=unused-argument
        """The JSON document of a ready transcript of the call.

        Readable by the users given access to the transcript and, when
        CALLS_TRANSCRIPTS_VISIBLE_TO_PARTICIPANTS is set, by the participants
        of the call.
        """
        session = self.get_object()

        try:
            transcript_id = uuid.UUID(transcript_id)
        except ValueError as e:
            raise drf_exceptions.NotFound("Transcript not found.") from e

        transcript = queries.call_transcripts(session).filter(pk=transcript_id).first()
        if transcript is None:
            raise drf_exceptions.NotFound("Transcript not found.")

        own_ids = queries.own_transcript_ids([transcript], request.user)
        if not queries.can_view_transcript(session, transcript, own_ids):
            raise drf_exceptions.PermissionDenied()

        if not transcript.is_ready:
            raise drf_exceptions.NotFound("The transcript is not ready.")

        key = transcript.get_file_key(TranscriptFormat.JSON)
        if key is None:
            raise drf_exceptions.NotFound("The transcript file is not available.")

        try:
            with default_storage.open(key, "rb") as transcript_file:
                document = json.loads(transcript_file.read())
        except FileNotFoundError as e:
            raise drf_exceptions.NotFound(
                "The transcript file is not available."
            ) from e
        except (BotoCoreError, ClientError, OSError, ValueError):
            logger.exception("Could not read transcript file %s", key)
            return drf_response.Response(
                {"error": "The transcript file could not be read."},
                status=drf_status.HTTP_502_BAD_GATEWAY,
            )

        return drf_response.Response(document)
