"""Tests of the room-metadata driven transcription control."""

import asyncio
import dataclasses
import hashlib
import hmac
import json
import logging

import httpx
import pytest

from tests.conftest import RecordingHook
from transcript_store import AgentHook, StoreConfig, TranscriptStore
from transcription_control import (
    TranscriptionController,
    parse_room_metadata,
    sign_transcription,
)

T0 = 1_792_404_000.0  # 2026-10-19T10:00:00Z
ROOM = "room-uuid"
TOKEN = "agent-token"
CONFIG = StoreConfig(
    bucket="bucket",
    hook_url="http://backend.test/hook/",
    hook_token=TOKEN,
    finalize_grace_s=0,
)
CONFIG_WITH_GRACE = dataclasses.replace(CONFIG, finalize_grace_s=2)
SIGNED = object()  # sign the entry like the backend does


def backend_sig(transcript_id: str, room: str = ROOM, token: str = TOKEN) -> str:
    """Signature computed like the backend (transcripts.models)."""
    return hmac.new(
        token.encode(), f"{room}:{transcript_id}".encode(), hashlib.sha256
    ).hexdigest()


def metadata(transcript_id=None, status="started", sig=SIGNED, **extra) -> str:
    """Room metadata as written by the backend (other keys are kept)."""
    data = {"recording_status": "started", "access_level": "public", **extra}
    if transcript_id is not None:
        data["transcription"] = {
            "id": transcript_id,
            "status": status,
            "room_name": "Планёрка",
            "started_at": "2026-10-19T10:00:00.000Z",
            "started_by": "Иван Петров",
        }
        if sig is SIGNED:
            data["transcription"]["sig"] = backend_sig(transcript_id)
        elif sig is not None:
            data["transcription"]["sig"] = sig
    return json.dumps(data)


def parse(raw, room_id=ROOM, token=TOKEN):
    """Parse the metadata as the agent of ROOM."""
    return parse_room_metadata(raw, room_id=room_id, token=token)


class Harness:
    """Controller wired to the fake S3 and a recording hook."""

    def __init__(self, fake_s3, *, failing_ids=()):
        """Build the controller."""
        self.s3 = fake_s3
        self.hook = RecordingHook()
        self.hook_client = AgentHook(
            CONFIG.hook_url,
            "token",
            backoff_s=0.001,
            transport=httpx.MockTransport(self.hook),
        )
        self.opened: list[str] = []
        self.failing_ids = set(failing_ids)
        self.controller = TranscriptionController(
            room_id=ROOM,
            config=CONFIG,
            store_factory=self.factory,
            hook=self.hook_client,
        )

    def factory(self, request) -> TranscriptStore:
        """Build an S3-backed store for the request."""
        if request.id in self.failing_ids:
            raise ValueError("bad S3 settings")
        self.opened.append(request.id)
        return TranscriptStore(
            transcript_id=request.id,
            room_id="room-uuid",
            room_name=request.room_name,
            started_at_ms=request.started_at_ms,
            config=CONFIG,
            s3_client=self.s3,
            hook=self.hook_client,
            clock=lambda: T0,
        )

    def say(self, text: str, start: float = T0 + 1):
        """A final phrase of one participant."""
        self.controller.add_final(
            identity="u1", name="Иван", start=start, end=start + 1, text=text
        )

    async def settle(self):
        """Wait for the background tasks (finalizations)."""
        while self.controller._tasks:
            await next(iter(self.controller._tasks))

    def statuses(self) -> dict[str, str]:
        """Hook status by transcript id."""
        return {p["transcript_id"]: p["status"] for p in self.hook.payloads}

    def texts(self, transcript_id: str) -> list[str]:
        """Segments of a finalized transcript."""
        document = self.s3.json(f"transcriptions/{transcript_id}/transcript.json")
        return [s["text"] for s in document["segments"]]


def test_parse_room_metadata():
    """Only a well-formed running transcription is returned."""
    request = parse(metadata("t1"))
    assert request.id == "t1"
    assert request.status == "started"
    assert request.room_name == "Планёрка"
    assert request.started_at_ms == round(T0 * 1000)
    assert request.started_by == "Иван Петров"
    assert parse(metadata("t1", status="stopping")).status == "stopping"
    for raw in (
        None,
        "",
        "not json",
        "[]",
        metadata(),
        json.dumps({"transcription": "t1"}),
        json.dumps({"transcription": {"id": "", "status": "started"}}),
        json.dumps({"transcription": {"id": "t1", "status": "done"}}),
        json.dumps({"transcription": {"id": "../t1", "status": "started"}}),
        json.dumps({"transcription": {"id": "a/b", "status": "started"}}),
        json.dumps({"transcription": {"id": 1, "status": "started"}}),
    ):
        assert parse(raw) is None
    uuid_id = "7c7e3b5c-1d7a-4b8e-9a51-0e1f2a3b4c5d"
    assert parse(metadata(uuid_id)).id == uuid_id
    no_date = json.dumps(
        {"transcription": {"id": "t1", "status": "started", "sig": backend_sig("t1")}}
    )
    assert parse(no_date).started_at_ms is None


def test_signature_matches_the_backend():
    """The agent computes the backend signature of "<room>:<transcript id>"."""
    uuid_id = "7c7e3b5c-1d7a-4b8e-9a51-0e1f2a3b4c5d"

    assert sign_transcription(ROOM, uuid_id, TOKEN) == backend_sig(uuid_id)
    assert sign_transcription(ROOM, uuid_id, TOKEN) == (
        hmac.new(
            b"agent-token",
            b"room-uuid:7c7e3b5c-1d7a-4b8e-9a51-0e1f2a3b4c5d",
            hashlib.sha256,
        ).hexdigest()
    )


def test_signature_known_answer():
    """Known answer shared with the backend tests (test_models_transcripts.py)."""
    room = "0f8c2a6e-3b1d-4f5e-9a7b-1c2d3e4f5a6b"
    transcript_id = "7c7e3b5c-1d7a-4b8e-9a51-0e1f2a3b4c5d"
    sig = "896234be7c7ff4d8ffc32a586a8a179142f06a58e5781c671c4008b54bda0eaa"
    raw = json.dumps(
        {"transcription": {"id": transcript_id, "status": "started", "sig": sig}}
    )

    assert sign_transcription(room, transcript_id, TOKEN) == sig
    assert parse(raw, room_id=room).id == transcript_id


@pytest.mark.parametrize(
    "sig",
    [
        None,  # missing
        "",
        "0" * 64,
        backend_sig("t1").upper(),
        backend_sig("t1")[:-1],
        backend_sig("t2"),  # signed for another transcript
        backend_sig("t1", room="other-room"),  # signed for another room
        backend_sig("t1", token="other-token"),  # signed with another key
        42,
        ["not", "a", "string"],
        "подпись",
    ],
)
def test_parse_room_metadata_rejects_bad_signatures(sig, caplog):
    """Entries without a valid signature for this room are ignored (warning)."""
    with caplog.at_level(logging.WARNING, logger="transcription-control"):
        assert parse(metadata("t1", sig=sig)) is None

    assert "missing or invalid signature for room room-uuid" in caplog.text


def test_parse_room_metadata_signed_for_another_room():
    """An entry copied from another room only works in that room."""
    raw = metadata("t1", sig=backend_sig("t1", room="other-room"))

    assert parse(raw) is None
    assert parse(raw, room_id="other-room").id == "t1"


def test_parse_room_metadata_without_token(caplog):
    """Without TRANSCRIPTION_AGENT_TOKEN no entry can be verified: all ignored."""
    raw = metadata("t1", sig=backend_sig("t1", token=""))

    with caplog.at_level(logging.WARNING, logger="transcription-control"):
        assert parse(raw, token="") is None

    assert "TRANSCRIPTION_AGENT_TOKEN is not set" in caplog.text


async def test_start_then_stopping_finalizes(fake_s3):
    """Started opens a store, stopping finalizes it (ready)."""
    h = Harness(fake_s3)
    h.say("до старта")

    await h.controller.apply_metadata(metadata("t1"))
    await h.controller.apply_metadata(metadata("t1"))  # repeated: no-op
    h.say("во время")
    await h.controller.apply_metadata(metadata("t1", status="stopping"))
    await h.settle()
    h.say("после")

    assert h.opened == ["t1"]
    assert h.controller.current is None
    assert h.statuses() == {"t1": "ready"}
    assert h.texts("t1") == ["во время"]
    assert h.hook.payloads[0]["room_id"] == ROOM


async def test_unsigned_entry_opens_nothing(fake_s3):
    """A forged entry (e.g. another room's transcript id) is never written to."""
    h = Harness(fake_s3)

    await h.controller.apply_metadata(metadata("t1", sig=None))
    await h.controller.apply_metadata(
        metadata("t1", sig=backend_sig("t1", room="other-room"))
    )
    h.say("раз")
    await h.controller.aclose()

    assert h.opened == []
    assert h.hook.requests == []
    assert fake_s3.keys() == []


async def test_unsigned_entry_is_handled_as_absent(fake_s3):
    """A running transcription replaced by a forged entry is finalized."""
    h = Harness(fake_s3)
    await h.controller.apply_metadata(metadata("t1"))
    h.say("раз")

    await h.controller.apply_metadata(metadata("t2", sig=backend_sig("t2", "other")))
    await h.settle()

    assert h.opened == ["t1"]
    assert h.controller.current is None
    assert h.statuses() == {"t1": "ready"}
    assert h.texts("t1") == ["раз"]


async def test_metadata_removed_finalizes(fake_s3):
    """The key disappearing (e.g. backend cleanup) also finalizes."""
    h = Harness(fake_s3)
    await h.controller.apply_metadata(metadata("t1"))
    h.say("раз")

    await h.controller.apply_metadata(metadata())
    await h.settle()

    assert h.statuses() == {"t1": "ready"}
    assert h.texts("t1") == ["раз"]


async def test_replaced_id_finalizes_old_and_opens_new(fake_s3):
    """A new id replaces the running transcription."""
    h = Harness(fake_s3)
    await h.controller.apply_metadata(metadata("t1"))
    h.say("первая")

    await h.controller.apply_metadata(metadata("t2"))
    h.say("вторая", start=T0 + 2)
    await h.settle()

    assert h.opened == ["t1", "t2"]
    assert h.controller.current.transcript_id == "t2"
    assert h.statuses() == {"t1": "ready"}
    assert h.texts("t1") == ["первая"]

    await h.controller.aclose()  # the call ends
    assert h.statuses() == {"t1": "ready", "t2": "ready"}
    assert h.texts("t2") == ["вторая"]


async def test_job_started_while_stopping_finalizes_at_once(fake_s3):
    """A (restarted) job seeing ``stopping`` resumes and finalizes."""
    h = Harness(fake_s3)

    await h.controller.apply_metadata(metadata("t1", status="stopping"))
    await h.settle()

    assert h.opened == ["t1"]
    assert h.controller.current is None
    assert h.statuses() == {"t1": "ready"}


async def test_finalized_id_is_not_reopened(fake_s3):
    """Metadata still naming a finalized id is ignored."""
    h = Harness(fake_s3)
    await h.controller.apply_metadata(metadata("t1"))
    await h.controller.apply_metadata(metadata("t1", status="stopping"))
    await h.settle()

    await h.controller.apply_metadata(metadata("t1", status="stopping"))
    await h.controller.apply_metadata(metadata("t1"))
    await h.settle()

    assert h.opened == ["t1"]
    assert len(h.hook.requests) == 1


async def test_room_metadata_event_and_close(fake_s3):
    """The rtc event handler applies changes; aclose finalizes and stops."""
    h = Harness(fake_s3)

    h.controller.on_room_metadata_changed("{}", metadata("t1"))
    await h.settle()
    h.say("раз")
    await h.controller.aclose()
    h.controller.on_room_metadata_changed("{}", metadata("t2"))
    await h.settle()

    assert h.opened == ["t1"]
    assert h.statuses() == {"t1": "ready"}
    assert h.texts("t1") == ["раз"]


async def test_store_that_cannot_open_is_reported_failed(fake_s3):
    """The backend is told when a transcription cannot even start."""
    h = Harness(fake_s3, failing_ids={"t1"})

    await h.controller.apply_metadata(metadata("t1"))

    assert h.controller.current is None
    (payload,) = h.hook.payloads
    assert payload["status"] == "failed"
    assert payload["room_id"] == ROOM
    assert "bad S3 settings" in payload["error"]


async def test_phrases_go_to_a_finalizing_store(fake_s3):
    """During the grace period the stopped store still takes late phrases."""
    h = Harness(fake_s3)
    recognized = asyncio.Event()
    waits = []

    async def wait_pending(timeout, *, started_before):
        waits.append((timeout, started_before))
        await recognized.wait()

    h.controller._wait_pending = wait_pending
    h.controller._config = CONFIG_WITH_GRACE
    await h.controller.apply_metadata(metadata("t1"))
    store = h.controller.current
    store._config = CONFIG_WITH_GRACE

    await h.controller.apply_metadata(metadata("t1", status="stopping"))
    await asyncio.sleep(0.01)  # the finalization waits for pending phrases
    assert waits == [(2, T0)]
    h.say("последняя", start=T0 - 0.5)  # started before the stop
    h.say("слишком поздно", start=T0 + 0.5)  # started after the stop
    recognized.set()
    await h.settle()

    assert h.texts("t1") == ["последняя"]
