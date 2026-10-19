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
                path(
                    "rooms/<uuid:pk>/transcription-settings/",
                    views.RoomTranscriptionViewSet.as_view(
                        {"get": "retrieve_settings", "put": "update_settings"}
                    ),
                    name="rooms-transcription-settings",
                ),
                path(
                    "transcription/models/",
                    views.TranscriptionModelsView.as_view(),
                    name="transcription-models",
                ),
                # Before the router: "agent-hook" and "agent-settings" would match a
                # transcript id
                path(
                    "transcripts/agent-hook/",
                    views.TranscriptAgentHookView.as_view(),
                    name="transcripts-agent-hook",
                ),
                path(
                    "transcripts/agent-settings/",
                    views.TranscriptAgentSettingsView.as_view(),
                    name="transcripts-agent-settings",
                ),
                *router.urls,
            ]
        ),
    ),
]
