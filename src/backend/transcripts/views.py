"""API endpoints of the transcripts application."""

from logging import getLogger

from django.conf import settings
from django.core.files.storage import default_storage
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.text import slugify

from botocore.exceptions import BotoCoreError, ClientError
from rest_framework import (
    decorators,
    mixins,
    negotiation,
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
from core.api.permissions import CanManageRecording
from core.api.viewsets import Pagination
from core.models import Room

from . import serializers, services
from .authentication import TranscriptionAgentAuthentication
from .models import Transcript, TranscriptFormat

logger = getLogger(__name__)

DOWNLOAD_CONTENT_TYPES = {
    TranscriptFormat.MD: "text/markdown; charset=utf-8",
    TranscriptFormat.JSON: "application/json",
}


class RoomTranscriptionViewSet(viewsets.GenericViewSet):
    """Start and stop the transcription of a room.

    Same rules as the recording: room owners and administrators, plus the
    authenticated participants present in an open room when
    RECORDING_AUTHENTICATED_PARTICIPANTS_ENABLED is set. LiveKit tokens are not
    accepted: a real account is required.
    """

    permission_classes = [CanManageRecording]
    queryset = Room.all_objects.all()
    serializer_class = serializers.TranscriptSerializer

    def get_object(self):
        """Get the room by its id, then check permissions and soft deletion."""
        if not settings.TRANSCRIPTION_ENABLED:
            raise Http404

        room = get_object_or_404(Room.all_objects, pk=self.kwargs["pk"])
        self.check_object_permissions(self.request, room)
        ensure_room_not_deleted(room)
        return room

    def start(self, request, pk=None):  # pylint: disable=unused-argument
        """Start transcribing the room."""
        room = self.get_object()

        try:
            transcript = services.TranscriptionService().start(room, request.user)
        except services.TranscriptionAlreadyStartedError:
            return drf_response.Response(
                {
                    "error": f"A transcription is already in progress for room {room.slug}"
                },
                status=drf_status.HTTP_409_CONFLICT,
            )
        except services.RoomNotLiveError:
            return drf_response.Response(
                {"error": f"Nobody is connected to room {room.slug}"},
                status=drf_status.HTTP_409_CONFLICT,
            )
        except services.TranscriptionStartError:
            return drf_response.Response(
                {"error": f"Transcription failed to start for room {room.slug}"},
                status=drf_status.HTTP_502_BAD_GATEWAY,
            )

        return drf_response.Response(
            self.get_serializer(transcript).data, status=drf_status.HTTP_201_CREATED
        )

    def stop(self, request, pk=None):  # pylint: disable=unused-argument
        """Stop transcribing the room."""
        room = self.get_object()

        try:
            transcript = services.TranscriptionService().stop(room)
        except services.TranscriptionNotFoundError as e:
            raise drf_exceptions.NotFound(
                "No active transcription found for this room."
            ) from e
        except services.TranscriptionStopError:
            return drf_response.Response(
                {"error": f"Transcription failed to stop for room {room.slug}"},
                status=drf_status.HTTP_502_BAD_GATEWAY,
            )

        return drf_response.Response(self.get_serializer(transcript).data)


class DownloadContentNegotiation(negotiation.DefaultContentNegotiation):
    """Ignore the "format" query parameter, which selects the file to download.

    The file is returned as is; renderers are only used for error responses.
    """

    def select_renderer(self, request, renderers, format_suffix=None):
        """Use the first renderer whatever the "format" query parameter."""
        renderer = renderers[0]
        return renderer, renderer.media_type


class TranscriptViewSet(
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    """List, retrieve and download the transcripts of the logged-in user."""

    pagination_class = Pagination
    permission_classes = [drf_permissions.IsAuthenticated]
    queryset = Transcript.objects.select_related("room")
    serializer_class = serializers.TranscriptSerializer

    def get_queryset(self):
        """Restrict transcripts to the ones the user has access to."""
        return super().get_queryset().filter(users=self.request.user)

    @staticmethod
    def get_download_filename(transcript, file_format, user):
        """Name of a downloaded file: transcript-<room>-<YYYY-MM-DD-HHMM>.<ext>."""
        room = transcript.room
        room_part = room.slug or slugify(room.name, allow_unicode=True) or "room"
        started_at = timezone.localtime(transcript.started_at, user.timezone)
        return f"transcript-{room_part}-{started_at:%Y-%m-%d-%H%M}.{file_format}"

    @decorators.action(
        detail=True,
        methods=["get"],
        url_path="download",
        content_negotiation_class=DownloadContentNegotiation,
    )
    def download(self, request, pk=None):  # pylint: disable=unused-argument
        """Download a transcript file: ?format=md (default) or ?format=json."""
        transcript = self.get_object()

        file_format = request.query_params.get("format", TranscriptFormat.MD.value)
        if file_format not in TranscriptFormat.values:
            raise drf_exceptions.ValidationError(
                {"format": f"Must be one of: {', '.join(TranscriptFormat.values)}."}
            )

        if not transcript.is_ready:
            raise drf_exceptions.NotFound("The transcript is not ready.")

        key = transcript.get_file_key(file_format)
        if key is None:
            raise drf_exceptions.NotFound("The transcript file is not available.")

        try:
            transcript_file = default_storage.open(key, "rb")
            # Fetch the object now, so that storage errors are not raised while
            # the response is streamed.
            transcript_file.open()
        except FileNotFoundError as e:
            raise drf_exceptions.NotFound(
                "The transcript file is not available."
            ) from e
        except (BotoCoreError, ClientError, OSError):
            logger.exception("Could not read transcript file %s", key)
            return drf_response.Response(
                {"error": "The transcript file could not be read."},
                status=drf_status.HTTP_502_BAD_GATEWAY,
            )

        return FileResponse(
            transcript_file,
            as_attachment=True,
            filename=self.get_download_filename(transcript, file_format, request.user),
            content_type=DOWNLOAD_CONTENT_TYPES[file_format],
        )


class TranscriptAgentHookView(views.APIView):
    """Receive the result of a transcription from the transcriber agent.

    Authenticated with the TRANSCRIPTION_AGENT_TOKEN Bearer token; the endpoint
    does not exist when no token is configured.
    """

    authentication_classes = [TranscriptionAgentAuthentication]
    permission_classes = [drf_permissions.IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        """Hide the endpoint when the agent token is not configured."""
        if not settings.TRANSCRIPTION_AGENT_TOKEN:
            raise Http404
        super().initial(request, *args, **kwargs)

    def post(self, request):
        """Mark the transcript as ready or failed, then notify its initiator."""
        serializer = serializers.AgentHookSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            transcript = Transcript.objects.select_related("room", "started_by").get(
                pk=data["transcript_id"]
            )
        except Transcript.DoesNotExist as e:
            raise drf_exceptions.NotFound("Transcript not found.") from e

        if data["room_id"] != str(transcript.room_id):
            raise drf_exceptions.ValidationError(
                {"room_id": "The transcript does not belong to this room."}
            )

        files = data.get("files") or {}
        invalid_formats = sorted(
            file_format
            for file_format, key in files.items()
            if key and not transcript.owns_key(key)
        )
        if invalid_formats:
            raise drf_exceptions.ValidationError(
                {
                    "files": (
                        f"Keys must be stored under {transcript.key_prefix}: "
                        f"{', '.join(invalid_formats)}."
                    )
                }
            )

        became_ready = services.TranscriptionService().complete(transcript, data)
        if became_ready:
            services.notify_transcript_ready(transcript)

        return drf_response.Response(
            {"id": str(transcript.id), "status": transcript.status}
        )
