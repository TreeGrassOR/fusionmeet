"""Tests of the re-dispatch of the transcriber to transcriptions that lost it."""

import asyncio
import contextlib
import json
import logging
from types import SimpleNamespace

import pytest
from livekit import api
from livekit.agents import AgentServer, WorkerOptions

from transcription_control import sign_transcription
from transcription_reconciler import DISPATCH_GRACE_S, TranscriptionReconciler

T0 = 1_792_404_000.0  # 2026-10-19T10:00:00Z, start of the transcriptions
NOW = T0 + 600
AGENT = "multi-user-transcriber"
TOKEN = "agent-token"
AGENT_KIND = api.ParticipantInfo.Kind.AGENT
STANDARD_KIND = api.ParticipantInfo.Kind.STANDARD


def room_metadata(room, status="started", transcript_id=None, signed_for=None, **kw):
    """Room metadata with a transcription entry signed like the backend does."""
    transcript_id = transcript_id or f"t-{room}"
    entry = {
        "id": transcript_id,
        "status": status,
        "room_name": "Планёрка",
        "started_at": "2026-10-19T10:00:00.000Z",
        "started_by": "Иван",
        "sig": sign_transcription(signed_for or room, transcript_id, TOKEN),
        **kw,
    }
    return json.dumps({"recording_status": "saved", "transcription": entry})


class FakeRoomService:
    """RoomService stand-in serving fake rooms and participants."""

    def __init__(self, rooms, participants):
        """Serve ``rooms`` ({name: metadata}) and ``participants`` by room."""
        self.rooms = rooms
        self.participants = participants
        self.fail_rooms: Exception | None = None
        self.fail_participants: dict[str, Exception] = {}
        self.listed: list[str] = []

    async def list_rooms(self, request):
        """List the rooms."""
        assert isinstance(request, api.ListRoomsRequest)
        if self.fail_rooms is not None:
            raise self.fail_rooms
        return api.ListRoomsResponse(
            rooms=[
                api.Room(name=name, metadata=metadata)
                for name, metadata in self.rooms.items()
            ]
        )

    async def list_participants(self, request):
        """List the participants of a room."""
        assert isinstance(request, api.ListParticipantsRequest)
        self.listed.append(request.room)
        if (error := self.fail_participants.get(request.room)) is not None:
            raise error
        return api.ListParticipantsResponse(
            participants=[
                api.ParticipantInfo(identity=identity, kind=kind)
                for identity, kind in self.participants.get(request.room, [])
            ]
        )


class FakeDispatchService:
    """AgentDispatchService stand-in recording the dispatches."""

    def __init__(self):
        """No dispatch yet."""
        self.created: list[tuple[str, str]] = []
        self.fail: dict[str, Exception] = {}

    async def create_dispatch(self, request):
        """Record the dispatch."""
        assert isinstance(request, api.CreateAgentDispatchRequest)
        if (error := self.fail.get(request.room)) is not None:
            raise error
        self.created.append((request.agent_name, request.room))
        return api.AgentDispatch(
            id=f"AD_{len(self.created)}",
            agent_name=request.agent_name,
            room=request.room,
        )


class FakeLiveKit:
    """Factory of fake LiveKitAPI clients sharing the same fake server state."""

    def __init__(self, rooms=None, participants=None):
        """Init the fake server."""
        self.room = FakeRoomService(rooms or {}, participants or {})
        self.agent_dispatch = FakeDispatchService()
        self.opened = 0
        self.closed = 0
        self.passes = asyncio.Queue()

    def __call__(self):
        """Build a client (like ``api.LiveKitAPI()``)."""
        return self

    async def __aenter__(self):
        """Open the client."""
        self.opened += 1
        return self

    async def __aexit__(self, *exc):
        """Close the client."""
        self.closed += 1
        self.passes.put_nowait(self.closed)

    @property
    def dispatched(self) -> list[str]:
        """Rooms the transcriber was dispatched to."""
        return [room for agent, room in self.agent_dispatch.created if agent == AGENT]


def make_reconciler(livekit, **kwargs) -> TranscriptionReconciler:
    """Reconciler on the fake LiveKit, at NOW."""
    return TranscriptionReconciler(
        agent_name=AGENT,
        token=TOKEN,
        api_factory=livekit,
        clock=lambda: NOW,
        **kwargs,
    )


def transcriber(room: str) -> tuple[str, int]:
    """The transcriber participant of a room."""
    return f"{AGENT}-{room}", AGENT_KIND


async def test_dispatches_to_transcriptions_without_transcriber():
    """Started and stopping transcriptions get a transcriber when it is missing."""
    livekit = FakeLiveKit(
        rooms={
            "crashed": room_metadata("crashed"),
            "stopping": room_metadata("stopping", status="stopping"),
            "healthy": room_metadata("healthy"),
            "no-transcription": json.dumps({"recording_status": "saved"}),
            "no-metadata": "",
            "other-agent": room_metadata("other-agent"),
            "human-twin": room_metadata("human-twin"),
        },
        participants={
            "crashed": [("u1", STANDARD_KIND)],
            "healthy": [("u1", STANDARD_KIND), transcriber("healthy")],
            "no-transcription": [("u1", STANDARD_KIND)],
            "other-agent": [("metadata-collector-other-agent", AGENT_KIND)],
            # only an agent with the identity counts, like the job request handler
            "human-twin": [(f"{AGENT}-human-twin", STANDARD_KIND)],
        },
    )
    reconciler = make_reconciler(livekit)

    dispatched = await reconciler.reconcile_once()

    expected = ["crashed", "stopping", "other-agent", "human-twin"]
    assert dispatched == expected
    assert livekit.dispatched == expected
    # rooms without a transcription are not even queried
    assert "no-transcription" not in livekit.room.listed
    assert "no-metadata" not in livekit.room.listed
    assert livekit.opened == livekit.closed == 1


async def test_logs_each_dispatch(caplog):
    """The re-dispatch is logged at info with the transcript and the room."""
    livekit = FakeLiveKit(rooms={"r1": room_metadata("r1", status="stopping")})

    with caplog.at_level(logging.INFO, logger="transcription-reconciler"):
        await make_reconciler(livekit).reconcile_once()

    (record,) = [r for r in caplog.records if r.name == "transcription-reconciler"]
    assert record.levelno == logging.INFO
    assert "t-r1 (stopping)" in record.getMessage()
    assert "room r1" in record.getMessage()
    assert "AD_1" in record.getMessage()


async def test_ignores_entries_not_signed_for_the_room(caplog):
    """Only backend-written entries count: forged or copied ones are ignored."""
    livekit = FakeLiveKit(
        rooms={
            "copied": room_metadata("copied", signed_for="other-room"),
            "unsigned": json.dumps(
                {"transcription": {"id": "t1", "status": "started"}}
            ),
            "bad-status": room_metadata("bad-status", status="ready"),
        }
    )

    with caplog.at_level(logging.WARNING, logger="transcription-control"):
        assert await make_reconciler(livekit).reconcile_once() == []

    assert livekit.dispatched == []
    assert livekit.room.listed == []
    assert "missing or invalid signature for room copied" in caplog.text


async def test_wrong_token_dispatches_nothing():
    """Entries are verified with the agent token."""
    livekit = FakeLiveKit(rooms={"r1": room_metadata("r1")})
    reconciler = TranscriptionReconciler(
        agent_name=AGENT, token="other-token", api_factory=livekit, clock=lambda: NOW
    )

    assert await reconciler.reconcile_once() == []


@pytest.mark.parametrize(
    "age, status, dispatched",
    [
        (1, "started", False),  # the backend's own dispatch is still joining
        (DISPATCH_GRACE_S - 1, "started", False),
        (DISPATCH_GRACE_S + 1, "started", True),
        (1, "stopping", True),  # a stop does not dispatch anything
    ],
)
async def test_leaves_a_just_started_transcription_to_its_dispatch(
    age, status, dispatched
):
    """A transcription started a moment ago still has its own dispatch joining."""
    livekit = FakeLiveKit(rooms={"r1": room_metadata("r1", status=status)})
    reconciler = make_reconciler(livekit)
    reconciler._clock = lambda: T0 + age

    assert await reconciler.reconcile_once() == (["r1"] if dispatched else [])


async def test_entry_without_start_date_is_reconciled():
    """No started_at (older backend entry): the grace does not apply."""
    raw = json.loads(room_metadata("r1"))
    del raw["transcription"]["started_at"]
    livekit = FakeLiveKit(rooms={"r1": json.dumps(raw)})

    assert await make_reconciler(livekit).reconcile_once() == ["r1"]


async def test_skips_rooms_where_this_worker_runs_a_job():
    """A job of this worker that has not joined yet is not duplicated."""
    livekit = FakeLiveKit(rooms={"r1": room_metadata("r1"), "r2": room_metadata("r2")})
    reconciler = make_reconciler(livekit, busy_rooms=lambda: ["r1"])

    assert await reconciler.reconcile_once() == ["r2"]


async def test_api_errors_are_logged_and_never_raised(caplog):
    """Listing failures are warnings; one failing room does not block the others."""
    livekit = FakeLiveKit(
        rooms={
            "r1": room_metadata("r1"),
            "r2": room_metadata("r2"),
            "r3": room_metadata("r3"),
        }
    )
    livekit.room.fail_participants["r1"] = api.TwirpError(
        "unavailable", "LiveKit is restarting", status=503
    )
    livekit.agent_dispatch.fail["r2"] = ConnectionError("connection reset")
    reconciler = make_reconciler(livekit)

    with caplog.at_level(logging.WARNING, logger="transcription-reconciler"):
        assert await reconciler.reconcile_once() == ["r3"]
        livekit.room.fail_rooms = ConnectionError("LiveKit is down")
        assert await reconciler.reconcile_once() == []

    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 3
    assert "room r1" in warnings[0] and "LiveKit is restarting" in warnings[0]
    assert "room r2" in warnings[1] and "connection reset" in warnings[1]
    assert "could not check the transcriptions" in warnings[2]
    assert "LiveKit is down" in warnings[2]


async def test_missing_livekit_settings_are_logged(monkeypatch, caplog):
    """Without LIVEKIT_URL the pass fails with a warning, not an exception."""
    monkeypatch.delenv("LIVEKIT_URL", raising=False)
    reconciler = TranscriptionReconciler(agent_name=AGENT, token=TOKEN)

    with caplog.at_level(logging.WARNING, logger="transcription-reconciler"):
        assert await reconciler.reconcile_once() == []

    assert "url must be set" in caplog.text


async def test_run_checks_at_once_then_periodically():
    """The first pass runs immediately, the next ones every interval."""
    livekit = FakeLiveKit(rooms={"r1": room_metadata("r1")})
    reconciler = make_reconciler(livekit, interval_s=0.01)

    task = asyncio.create_task(reconciler.run())
    try:
        for expected in (1, 2, 3):
            assert await asyncio.wait_for(livekit.passes.get(), 2) == expected
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert livekit.dispatched[:3] == ["r1", "r1", "r1"]


async def test_run_survives_errors_and_pauses_while_draining():
    """Failing passes and the draining worker never stop the loop."""
    livekit = FakeLiveKit(rooms={"r1": room_metadata("r1")})
    draining = True
    calls = 0

    def paused():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("worker state unavailable")
        return draining

    reconciler = make_reconciler(livekit, interval_s=0.01, paused=paused)
    task = asyncio.create_task(reconciler.run())
    try:
        while calls < 4:
            await asyncio.sleep(0.01)
        assert livekit.opened == 0  # paused: LiveKit is not queried
        draining = False
        assert await asyncio.wait_for(livekit.passes.get(), 2) == 1
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert livekit.dispatched[0] == "r1"


class StubServer:
    """The parts of AgentServer used by the reconciler."""

    def __init__(self):
        """A registered, idle worker."""
        self.handlers = {}
        self.active_jobs = []
        self.draining = False

    def on(self, event, callback):
        """Register an event handler."""
        self.handlers.setdefault(event, []).append(callback)

    def emit(self, event, *args):
        """Emit an event."""
        for callback in self.handlers.get(event, []):
            callback(*args)


def running_job(room: str):
    """RunningJobInfo stand-in."""
    return SimpleNamespace(job=SimpleNamespace(room=SimpleNamespace(name=room)))


async def test_attach_starts_once_registered_and_uses_the_worker_state():
    """The loop starts on registration (once) and follows the worker's jobs."""
    livekit = FakeLiveKit(rooms={"r1": room_metadata("r1"), "r2": room_metadata("r2")})
    server = StubServer()
    server.active_jobs = [running_job("r1")]
    reconciler = make_reconciler(livekit, interval_s=3600)

    reconciler.attach(server)
    assert reconciler._task is None  # not before the worker is registered
    server.emit("worker_registered", "AW_1", None)
    task = reconciler._task
    server.emit("worker_registered", "AW_1", None)  # reconnection
    try:
        assert reconciler._task is task
        assert await asyncio.wait_for(livekit.passes.get(), 2) == 1
        assert livekit.dispatched == ["r2"]  # r1 has a job of this worker

        server.draining = True
        assert reconciler._paused()
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def test_attach_to_the_livekit_agent_server():
    """livekit-agents 1.7.0 AgentServer emits worker_registered and has the state."""

    async def entrypoint(ctx):
        pass

    server = AgentServer.from_server_options(
        WorkerOptions(entrypoint_fnc=entrypoint, agent_name=AGENT)
    )
    assert server.draining is False
    reconciler = make_reconciler(FakeLiveKit(), interval_s=3600)
    started = asyncio.Event()

    async def run():
        started.set()
        await asyncio.Event().wait()

    reconciler.run = run
    reconciler.attach(server)
    server.emit("worker_registered", "AW_1", None)
    try:
        await asyncio.wait_for(started.wait(), 2)
    finally:
        reconciler._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await reconciler._task


def test_from_env_requires_the_storage_and_token(monkeypatch):
    """Disabled without bucket, hook or token, or with an interval of 0."""
    monkeypatch.setenv("AWS_STORAGE_BUCKET_NAME", "bucket")
    monkeypatch.setenv("TRANSCRIPTION_HOOK_URL", "http://backend.test/hook/")
    monkeypatch.setenv("TRANSCRIPTION_AGENT_TOKEN", TOKEN)
    monkeypatch.delenv("TRANSCRIPTION_RECONCILE_INTERVAL_S", raising=False)

    reconciler = TranscriptionReconciler.from_env(AGENT)
    assert reconciler.interval_s == 20
    assert reconciler._token == TOKEN
    assert reconciler.transcriber_identity("r1") == f"{AGENT}-r1"

    monkeypatch.setenv("TRANSCRIPTION_RECONCILE_INTERVAL_S", "45")
    assert TranscriptionReconciler.from_env(AGENT).interval_s == 45
    monkeypatch.setenv("TRANSCRIPTION_RECONCILE_INTERVAL_S", "1")
    assert TranscriptionReconciler.from_env(AGENT).interval_s == 5  # floor
    monkeypatch.setenv("TRANSCRIPTION_RECONCILE_INTERVAL_S", "0")
    assert TranscriptionReconciler.from_env(AGENT) is None
    monkeypatch.delenv("TRANSCRIPTION_RECONCILE_INTERVAL_S")

    for name in (
        "TRANSCRIPTION_AGENT_TOKEN",
        "TRANSCRIPTION_HOOK_URL",
        "AWS_STORAGE_BUCKET_NAME",
    ):
        with monkeypatch.context() as patch:
            patch.setenv(name, "")
            assert TranscriptionReconciler.from_env(AGENT) is None
        assert TranscriptionReconciler.from_env(AGENT) is not None
