"""URL configuration of the transcripts application."""

from django.conf import settings
from django.urls import include, path

from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("transcripts", views.TranscriptViewSet, basename="transcripts")

urlpatterns = [
    path(
        f"api/{settings.API_VERSION}/",
        include(
            [
                path(
                    "rooms/<uuid:pk>/start-transcription/",
                    views.RoomTranscriptionViewSet.as_view({"post": "start"}),
                    name="rooms-start-transcription",
                ),
                path(
                    "rooms/<uuid:pk>/stop-transcription/",
                    views.RoomTranscriptionViewSet.as_view({"post": "stop"}),
                    name="rooms-stop-transcription",
                ),
                # Before the router: "agent-hook" would match a transcript id
                path(
                    "transcripts/agent-hook/",
                    views.TranscriptAgentHookView.as_view(),
                    name="transcripts-agent-hook",
                ),
                *router.urls,
            ]
        ),
    ),
]
