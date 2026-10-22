"""Ask LiveKit which calls are live and who is connected to them.

Webhooks can be lost, and clients only hold a token for a room: what LiveKit
answers now is the reference for chat messages and for open calls.
"""

# pylint: disable=no-name-in-module

import asyncio

import aiohttp
from asgiref.sync import async_to_sync
from livekit.api import ListRoomsRequest, RoomParticipantIdentity, TwirpError

from core import utils

# LiveKit answers within milliseconds: a request (or a page of the call list)
# never waits longer for it
LIVEKIT_TIMEOUT_SECONDS = 3


class LiveKitUnavailableError(Exception):
    """LiveKit could not be asked."""


@async_to_sync
async def _list_rooms(names):
    lkapi = utils.create_livekit_client()
    try:
        response = await asyncio.wait_for(
            lkapi.room.list_rooms(ListRoomsRequest(names=names)),
            LIVEKIT_TIMEOUT_SECONDS,
        )
    finally:
        await lkapi.aclose()
    return list(response.rooms)


@async_to_sync
async def _get_participant(room_name, identity):
    lkapi = utils.create_livekit_client()
    try:
        return await asyncio.wait_for(
            lkapi.room.get_participant(
                RoomParticipantIdentity(room=room_name, identity=identity)
            ),
            LIVEKIT_TIMEOUT_SECONDS,
        )
    except TwirpError as error:
        if error.code == "not_found":
            return None
        raise
    finally:
        await lkapi.aclose()


def get_live_rooms(room_ids):
    """The LiveKit rooms live now among the rooms of some ids, by room id.

    Raises:
        LiveKitUnavailableError: LiveKit did not answer.
    """
    names = sorted({str(room_id) for room_id in room_ids})
    try:
        rooms = _list_rooms(names)
    except (TwirpError, aiohttp.ClientError, TimeoutError, OSError) as error:
        raise LiveKitUnavailableError(str(error)) from error
    return {room.name: room for room in rooms if room.name in names}


def get_live_room(room_id):
    """The LiveKit room of a room id live now, None when there is none.

    Raises:
        LiveKitUnavailableError: LiveKit did not answer.
    """
    return get_live_rooms([room_id]).get(str(room_id))


def get_participant(room_id, identity):
    """The participant of an identity connected to a room, None when absent.

    Raises:
        LiveKitUnavailableError: LiveKit did not answer.
    """
    try:
        return _get_participant(str(room_id), identity)
    except (TwirpError, aiohttp.ClientError, TimeoutError, OSError) as error:
        raise LiveKitUnavailableError(str(error)) from error
