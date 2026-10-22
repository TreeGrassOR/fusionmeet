"""URL configuration of the calls application."""

from django.conf import settings
from django.urls import include, path

from rest_framework.routers import SimpleRouter

from . import views

router = SimpleRouter()
router.register("calls", views.CallViewSet, basename="calls")

urlpatterns = [
    path(
        f"api/{settings.API_VERSION}/",
        include(
            [
                # Before the router, whose detail route would match it
                path(
                    "calls/webhooks-livekit/",
                    views.CallsLiveKitWebhookView.as_view(),
                    name="calls-webhooks-livekit",
                ),
                path(
                    "rooms/<uuid:pk>/chat-messages/",
                    views.RoomChatMessagesView.as_view(),
                    name="rooms-chat-messages",
                ),
                path(
                    "rooms/<uuid:pk>/call-title/",
                    views.RoomCallTitleView.as_view(),
                    name="rooms-call-title",
                ),
                *router.urls,
            ]
        ),
    ),
]
