"""Shared helpers for tests in the calls application."""

# pylint: disable=no-member

import base64
import hashlib
import json
from datetime import datetime
from datetime import timezone as dt_timezone

from django.conf import settings
from django.contrib.auth.models import AnonymousUser

import aiohttp
from livekit import api
from rest_framework.test import APIClient

from core.utils import generate_token

WEBHOOK_URL = "/api/v1.0/calls/webhooks-livekit/"

# 2026-10-22 10:00:00 UTC
CALL_START = datetime(2026, 10, 22, 10, 0, tzinfo=dt_timezone.utc)


def epoch_seconds(moment):
    """Epoch seconds of a datetime, as LiveKit writes `createdAt`."""
    return int(moment.timestamp())


def epoch_milliseconds(moment):
    """Epoch milliseconds of a datetime."""
    return int(moment.timestamp() * 1000)


def sign_webhook(body):
    """Authorization header LiveKit sends with a webhook body."""
    token = api.AccessToken(
        settings.LIVEKIT_CONFIGURATION["api_key"],
        settings.LIVEKIT_CONFIGURATION["api_secret"],
    )
    token.claims.sha256 = base64.b64encode(
        hashlib.sha256(body.encode()).digest()
    ).decode()
    return token.to_jwt()


def post_webhook(event, *, authorization=None):
    """Post a signed LiveKit webhook to the calls endpoint."""
    body = json.dumps(event)
    return APIClient().post(
        WEBHOOK_URL,
        data=body,
        content_type="application/webhook+json",
        HTTP_AUTHORIZATION=authorization or sign_webhook(body),
    )


def participant_info(  # noqa: PLR0913  # pylint: disable=too-many-arguments
    identity,
    *,
    name="Иван Петров",
    kind="STANDARD",
    joined_at=CALL_START,
    is_authenticated=False,
    sid="PA_test",
):
    """A LiveKit ParticipantInfo, as serialized in webhooks."""
    return {
        "sid": sid,
        "identity": identity,
        "name": name,
        "kind": kind,
        "joinedAt": str(epoch_seconds(joined_at)),
        "joinedAtMs": str(epoch_milliseconds(joined_at)),
        "attributes": {
            "color": "hsl(1, 50%, 50%)",
            "is_authenticated": "true" if is_authenticated else "false",
        },
    }


def webhook_event(  # noqa: PLR0913  # pylint: disable=too-many-arguments
    event,
    room_name,
    *,
    sid="RM_first",
    at=CALL_START,
    room_created_at=CALL_START,
    participant=None,
    event_id="EV_test",
):
    """A LiveKit webhook event for a room."""
    data = {
        "event": event,
        "room": {
            "sid": sid,
            "name": str(room_name),
            "emptyTimeout": 300,
            "departureTimeout": 20,
            "creationTime": str(epoch_seconds(room_created_at)),
            "creationTimeMs": str(epoch_milliseconds(room_created_at)),
        },
        "id": event_id,
        "createdAt": str(epoch_seconds(at)),
    }
    if participant is not None:
        data["participant"] = participant
    return data


def livekit_token(room, user=None, username=None):
    """A LiveKit token to join a room, for a user or a guest."""
    return generate_token(
        room=str(room.id), user=user or AnonymousUser(), username=username
    )


def livekit_client(token):
    """An API client authenticated with a LiveKit token."""
    client = APIClient()
    client.credentials(HTTP_AUTHORIZATION=f"X-LiveKit-Token {token}")
    return client


def logged_in_client(user):
    """An API client logged in as a user."""
    client = APIClient()
    client.force_login(user)
    return client


class FakeLiveKit:
    """LiveKit as the calls application asks it: the rooms live now and the
    participants connected to them.

    It does not answer until a room is started: the calls of the tests then
    stay as they are recorded.
    """

    def __init__(self):
        self.available = False
        self.rooms = {}
        self.participants = {}
        self.requests = []

    def start_room(self, room, *, sid="RM_first", created_at=CALL_START):
        """Make a LiveKit room instance live for a room."""
        self.available = True
        self.rooms[str(room.id)] = api.Room(
            sid=sid,
            name=str(room.id),
            creation_time=epoch_seconds(created_at),
            creation_time_ms=epoch_milliseconds(created_at),
        )

    def stop_room(self, room):
        """Close the LiveKit room instance of a room."""
        self.rooms.pop(str(room.id), None)
        self.participants = {
            key: value
            for key, value in self.participants.items()
            if key[0] != str(room.id)
        }

    def connect(  # noqa: PLR0913  # pylint: disable=too-many-arguments
        self,
        room,
        identity,
        *,
        name="Иван Петров",
        is_authenticated=False,
        kind=api.ParticipantInfo.STANDARD,
        joined_at=CALL_START,
    ):
        """Connect a participant to the live room instance of a room."""
        self.available = True
        self.participants[(str(room.id), identity)] = api.ParticipantInfo(
            sid=f"PA_{identity}",
            identity=identity,
            name=name,
            kind=kind,
            state=api.ParticipantInfo.ACTIVE,
            joined_at=epoch_seconds(joined_at),
            joined_at_ms=epoch_milliseconds(joined_at),
            attributes={"is_authenticated": "true" if is_authenticated else "false"},
        )

    def disconnect(self, room, identity):
        """Disconnect a participant from a room."""
        self.participants.pop((str(room.id), identity), None)

    def _answer(self):
        if not self.available:
            raise aiohttp.ClientConnectionError("LiveKit is down")

    async def list_rooms(self, request):
        """RoomService.ListRooms"""
        self.requests.append(("list_rooms", sorted(request.names)))
        self._answer()
        return api.ListRoomsResponse(
            rooms=[
                room
                for name, room in self.rooms.items()
                if not request.names or name in request.names
            ]
        )

    async def get_participant(self, request):
        """RoomService.GetParticipant"""
        self.requests.append(("get_participant", request.room, request.identity))
        self._answer()
        participant = self.participants.get((request.room, request.identity))
        if participant is None:
            raise api.TwirpError("not_found", "participant does not exist", status=404)
        return participant

    def client(self, *args, **kwargs):  # pylint: disable=unused-argument
        """A LiveKit API client that asks this fake."""
        fake = self

        class Client:
            """LiveKitAPI"""

            room = fake

            async def aclose(self):
                """Close the client."""

        return Client()
