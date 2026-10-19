"""Tests of the transcript storage (S3 parts, final files, backend hook)."""

import asyncio
import json
import re

import httpx
import pytest

from tests.conftest import FakeS3, RecordingHook
from transcript_store import (
    AgentHook,
    Segment,
    StoreConfig,
    TranscriptStore,
    format_timestamp,
    list_models,
    merge_segments,
    parse_timestamp,
    render_markdown,
)

TID = "7c7e3b5c-1d7a-4b8e-9a51-0e1f2a3b4c5d"
# 2026-10-19T10:00:00Z
T0 = 1_792_404_000.0
PART_KEY = re.compile(rf"^transcriptions/{TID}/parts/\d{{13}}-\d{{6}}\.json$")


class Clock:
    """Settable wall clock."""

    def __init__(self, now: float = T0):
        """Start at ``now``."""
        self.now = now

    def __call__(self) -> float:
        """Current time."""
        return self.now


def make_store(fake_s3, *, hook=None, clock=None, started_at_ms=None, **config):
    """Build a store on the fake S3."""
    settings = {
        "bucket": "bucket",
        "hook_url": "http://backend.test/api/v1.0/transcripts/agent-hook/",
        "flush_interval_s": 3600,
        "finalize_grace_s": 0,
        **config,
    }
    return TranscriptStore(
        transcript_id=TID,
        room_id="room-uuid",
        room_name="Планёрка",
        started_at_ms=started_at_ms if started_at_ms is not None else round(T0 * 1000),
        config=StoreConfig(**settings),
        s3_client=fake_s3,
        hook=hook,
        clock=clock or Clock(),
    )


def make_hook(handler) -> AgentHook:
    """Hook posting through a mock transport, without real backoff."""
    return AgentHook(
        "http://backend.test/api/v1.0/transcripts/agent-hook/",
        "agent-token",
        backoff_s=0.001,
        transport=httpx.MockTransport(handler),
    )


def test_timestamps_round_trip():
    """Timestamps are ISO 8601 UTC with milliseconds and a Z suffix."""
    assert format_timestamp(1_792_404_005_123) == "2026-10-19T10:00:05.123Z"
    assert parse_timestamp("2026-10-19T10:00:05.123Z") == 1_792_404_005_123
    assert parse_timestamp("2026-10-19T13:00:05.123+03:00") == 1_792_404_005_123
    assert parse_timestamp("2026-10-19T10:00:05") == 1_792_404_005_000


async def test_flush_writes_new_part_objects_incrementally(fake_s3):
    """Every flush with new segments writes a new part with only those."""
    store = make_store(fake_s3)
    await store.open()
    store.add_segment(identity="u1", name="Иван", start=T0 + 1, end=T0 + 2, text="раз")
    store.add_segment(identity="u2", name="Мария", start=T0 + 3, end=T0 + 4, text="два")

    assert await store.flush()
    assert await store.flush()  # nothing new: no new object
    store.add_segment(identity="u1", name="Иван", start=T0 + 5, end=T0 + 6, text="три")
    assert await store.flush()

    keys = fake_s3.keys(f"transcriptions/{TID}/parts/")
    assert len(keys) == 2
    assert all(PART_KEY.match(k) for k in keys)
    assert [s["text"] for s in fake_s3.json(keys[0])] == ["раз", "два"]
    assert fake_s3.json(keys[1]) == [
        {
            "identity": "u1",
            "name": "Иван",
            "start": "2026-10-19T10:00:05.000Z",
            "end": "2026-10-19T10:00:06.000Z",
            "text": "три",
        }
    ]
    await store.finalize()


async def test_periodic_flush(fake_s3):
    """Buffered segments reach S3 without an explicit flush."""
    store = make_store(fake_s3, flush_interval_s=0.02)
    await store.open()
    store.add_segment(identity="u1", name="Иван", start=T0, end=T0 + 1, text="раз")

    for _ in range(100):
        if fake_s3.keys(f"transcriptions/{TID}/parts/"):
            break
        await asyncio.sleep(0.01)

    assert len(fake_s3.keys(f"transcriptions/{TID}/parts/")) == 1
    await store.finalize()


async def test_failed_flush_keeps_segments_for_the_next_one(fake_s3):
    """A failed part write is retried later with the same segments."""
    store = make_store(fake_s3)
    await store.open()
    store.add_segment(identity="u1", name="Иван", start=T0, end=T0 + 1, text="раз")
    fake_s3.fail_puts = 1

    assert not await store.flush()
    assert await store.flush()

    (key,) = fake_s3.keys(f"transcriptions/{TID}/parts/")
    assert [s["text"] for s in fake_s3.json(key)] == ["раз"]
    await store.finalize()


async def test_resume_from_existing_parts(fake_s3):
    """A restarted store reads the parts back and finalizes everything."""
    previous = make_store(fake_s3)
    await previous.open()
    previous.add_segment(
        identity="u1", name="Иван", start=T0 + 1, end=T0 + 2, text="раз"
    )
    previous.add_segment(
        identity="u2", name="Мария", start=T0 + 2, end=T0 + 3, text="два"
    )
    await previous.flush()
    previous.add_segment(
        identity="u1", name="Иван", start=T0 + 4, end=T0 + 5, text="три"
    )
    await previous.flush()
    # the crashed process never finalized; a new job resumes 10 s later
    restarted = make_store(fake_s3, clock=Clock(T0 + 10))

    await restarted.open()

    assert [s.text for s in restarted.segments] == ["раз", "два", "три"]
    restarted.add_segment(
        identity="u2", name="Мария", start=T0 + 11, end=T0 + 12, text="четыре"
    )
    await restarted.flush()
    keys = fake_s3.keys(f"transcriptions/{TID}/parts/")
    assert len(keys) == 3  # listing is paginated by 2 in the fake
    assert keys[-1].endswith("-000002.json")  # sequence continues
    assert await restarted.finalize()
    document = fake_s3.json(f"transcriptions/{TID}/transcript.json")
    assert [s["text"] for s in document["segments"]] == ["раз", "два", "три", "четыре"]


async def test_finalize_dedupes_parts_written_twice(fake_s3):
    """Identical segments found in several parts are kept once."""
    segment = Segment(
        "u1", "Иван", round(T0 * 1000) + 1000, round(T0 * 1000) + 2000, "раз"
    )
    for key in ("1792404001000-000000", "1792404002000-000000"):
        fake_s3.put_object(
            Bucket="bucket",
            Key=f"transcriptions/{TID}/parts/{key}.json",
            Body=json.dumps([segment.to_json()]).encode(),
        )
    store = make_store(fake_s3)
    await store.open()

    assert await store.finalize()

    assert len(fake_s3.json(f"transcriptions/{TID}/transcript.json")["segments"]) == 1


async def test_finalize_writes_json_and_markdown_and_calls_hook(fake_s3):
    """The final files follow the documented format; the backend is notified."""
    hook = RecordingHook()
    clock = Clock()
    store = make_store(fake_s3, hook=make_hook(hook), clock=clock)
    await store.open()
    store.add_segment(
        identity="u1",
        name="Иван Петров",
        start=T0 + 5.2,
        end=T0 + 9,
        text="текст  фразы",
    )
    store.add_segment(
        identity="u2", name="Мария Соколова", start=T0 + 12.5, end=T0 + 14, text="текст"
    )
    await store.flush()
    store.add_segment(
        identity="u1", name="Иван Петров", start=T0 + 3725, end=T0 + 3730, text="ещё"
    )
    clock.now = T0 + 72 * 60  # 14:12 Moscow time

    assert await store.finalize()

    json_key = f"transcriptions/{TID}/transcript.json"
    md_key = f"transcriptions/{TID}/transcript.md"
    assert fake_s3.json(json_key) == {
        "version": 1,
        "transcript_id": TID,
        "language": "ru",
        "room": {"id": "room-uuid", "name": "Планёрка"},
        "started_at": "2026-10-19T10:00:00.000Z",
        "ended_at": "2026-10-19T11:12:00.000Z",
        "participants": [
            {"identity": "u1", "name": "Иван Петров"},
            {"identity": "u2", "name": "Мария Соколова"},
        ],
        "models": [],  # an older ASR service reports no model
        "segments": [
            {
                "identity": "u1",
                "name": "Иван Петров",
                "start": "2026-10-19T10:00:05.200Z",
                "end": "2026-10-19T10:00:09.000Z",
                "text": "текст фразы",
            },
            {
                "identity": "u2",
                "name": "Мария Соколова",
                "start": "2026-10-19T10:00:12.500Z",
                "end": "2026-10-19T10:00:14.000Z",
                "text": "текст",
            },
            {
                "identity": "u1",
                "name": "Иван Петров",
                "start": "2026-10-19T11:02:05.000Z",
                "end": "2026-10-19T11:02:10.000Z",
                "text": "ещё",
            },
        ],
    }
    assert fake_s3.text(md_key) == (
        "# Расшифровка встречи «Планёрка»\n"
        "\n"
        "- Дата: 19.10.2026, 13:00–14:12 (Europe/Moscow)\n"
        "- Участники: Иван Петров, Мария Соколова\n"
        "\n"
        "[00:00:05] **Иван Петров**: текст фразы\n"
        "\n"
        "[00:00:12] **Мария Соколова**: текст\n"
        "\n"
        "[01:02:05] **Иван Петров**: ещё\n"
    )
    assert fake_s3.objects[("bucket", md_key)][1] == "text/markdown; charset=utf-8"

    (request,) = hook.requests
    assert str(request.url) == "http://backend.test/api/v1.0/transcripts/agent-hook/"
    assert request.headers["authorization"] == "Bearer agent-token"
    assert request.headers["content-type"] == "application/json"
    assert hook.payloads == [
        {
            "transcript_id": TID,
            # the backend checks that the transcript belongs to this room
            "room_id": "room-uuid",
            "status": "ready",
            "started_at": "2026-10-19T10:00:00.000Z",
            "ended_at": "2026-10-19T11:12:00.000Z",
            "participants": [
                {"identity": "u1", "name": "Иван Петров"},
                {"identity": "u2", "name": "Мария Соколова"},
            ],
            "segments_count": 3,
            "models": [],
            "files": {"json": json_key, "md": md_key},
            "error": None,
        }
    ]


async def test_finalize_is_idempotent(fake_s3):
    """Finalizing twice writes and notifies once."""
    hook = RecordingHook()
    store = make_store(fake_s3, hook=make_hook(hook))
    await store.open()

    first, second = store.finalize(), store.finalize()

    assert first is second
    assert await first
    assert len(hook.requests) == 1


async def test_hook_failures_are_retried(fake_s3):
    """Network errors and 5xx answers are retried."""
    hook = RecordingHook(statuses=[503, 502])
    store = make_store(fake_s3, hook=make_hook(hook))
    await store.open()

    assert await store.finalize()

    assert len(hook.requests) == 3
    assert {p["status"] for p in hook.payloads} == {"ready"}


async def test_hook_gives_up_after_the_retries():
    """The hook is tried 1 + 5 times, 4xx answers are not retried."""
    always_down = RecordingHook(statuses=[500] * 10)
    assert not await make_hook(always_down).notify({"transcript_id": TID})
    assert len(always_down.requests) == 6

    rejected = RecordingHook(statuses=[401])
    assert not await make_hook(rejected).notify({"transcript_id": TID})
    assert len(rejected.requests) == 1

    calls = []

    def unreachable(request):
        calls.append(request)
        if len(calls) < 3:
            raise httpx.ConnectError("refused")
        return httpx.Response(200)

    assert await make_hook(unreachable).notify({"transcript_id": TID})
    assert len(calls) == 3


async def test_failure_posts_status_failed(fake_s3):
    """When the final files cannot be written the backend gets ``failed``."""
    hook = RecordingHook()
    store = make_store(fake_s3, hook=make_hook(hook))
    await store.open()
    store.add_segment(identity="u1", name="Иван", start=T0 + 1, end=T0 + 2, text="раз")
    fake_s3.fail_put_keys = {"transcript.json"}

    assert not await store.finalize()

    (payload,) = hook.payloads
    assert payload["status"] == "failed"
    assert payload["room_id"] == "room-uuid"
    assert payload["files"] == {"json": None, "md": None}
    assert payload["segments_count"] == 1
    assert payload["models"] == []
    assert "ClientError" in payload["error"]


async def test_only_phrases_started_while_open_are_kept(fake_s3):
    """Speech started before the store opened or after the stop is ignored."""
    clock = Clock(T0 + 100)
    store = make_store(fake_s3, clock=clock)
    await store.open()

    assert not store.add_segment(
        identity="u1", name="И", start=T0 + 98, end=T0 + 101, text="до"
    )
    assert store.add_segment(
        identity="u1", name="И", start=T0 + 99.5, end=T0 + 101, text="почти"
    )
    assert not store.add_segment(
        identity="u1", name="И", start=T0 + 101, end=T0 + 102, text=" "
    )
    clock.now = T0 + 110
    task = store.finalize()
    assert store.add_segment(
        identity="u1", name="И", start=T0 + 109, end=T0 + 111, text="в конце"
    )
    assert not store.add_segment(
        identity="u1", name="И", start=T0 + 110.5, end=T0 + 112, text="после"
    )
    await task

    assert [s.text for s in store.segments] == ["почти", "в конце"]
    assert not store.add_segment(
        identity="u1", name="И", start=T0 + 109, end=T0 + 112, text="поздно"
    )


async def test_finalize_waits_for_pending_phrases(fake_s3):
    """Phrases recognized during the grace period are still included."""
    store = make_store(fake_s3, finalize_grace_s=1)
    await store.open()
    calls = []

    async def wait_pending(timeout, *, started_before):
        calls.append((timeout, started_before))
        store.add_segment(
            identity="u1", name="Иван", start=T0, end=T0 + 1, text="успел"
        )

    assert await store.finalize(wait_pending=wait_pending)

    assert calls == [(1, T0)]
    document = fake_s3.json(f"transcriptions/{TID}/transcript.json")
    assert [s["text"] for s in document["segments"]] == ["успел"]


def test_markdown_header_variants():
    """Day change, unknown time zone, no participant, escaped names."""
    start_ms = round(T0 * 1000) + 10 * 3600 * 1000  # 23:00 Moscow time
    segments = [Segment("u1", "Ива*н", start_ms + 1000, start_ms + 2000, "раз")]

    markdown = render_markdown(
        room_name="Ночь",
        started_ms=start_ms,
        ended_ms=start_ms + 2 * 3600 * 1000,
        participants=[{"identity": "u1", "name": "Ива*н"}],
        segments=segments,
        timezone="Europe/Moscow",
    )
    assert "- Дата: 19.10.2026, 23:00 – 20.10.2026, 01:00 (Europe/Moscow)" in markdown
    assert "[00:00:01] **Ива\\*н**: раз" in markdown

    empty = render_markdown(
        room_name="Тишина",
        started_ms=round(T0 * 1000),
        ended_ms=round(T0 * 1000) + 60_000,
        participants=[],
        segments=[],
        timezone="Mars/Olympus",
    )
    assert empty == (
        "# Расшифровка встречи «Тишина»\n"
        "\n"
        "- Дата: 19.10.2026, 10:00–10:01 (UTC)\n"
        "- Участники: —\n"
    )


def test_config_from_environment(monkeypatch):
    """Storage is only enabled with a bucket and a hook URL."""
    monkeypatch.delenv("AWS_STORAGE_BUCKET_NAME", raising=False)
    monkeypatch.setenv("TRANSCRIPTION_HOOK_URL", "http://backend/hook/")
    assert StoreConfig.from_env() is None

    monkeypatch.setenv("AWS_STORAGE_BUCKET_NAME", "meet-media-storage")
    monkeypatch.setenv("TRANSCRIPTION_AGENT_TOKEN", "t")
    monkeypatch.setenv("TRANSCRIPTION_S3_PREFIX", "/custom/")
    monkeypatch.setenv("TRANSCRIPT_FLUSH_INTERVAL_S", "2.5")
    config = StoreConfig.from_env()
    assert config == StoreConfig(
        bucket="meet-media-storage",
        hook_url="http://backend/hook/",
        hook_token="t",
        prefix="custom",
        flush_interval_s=2.5,
    )

    monkeypatch.setenv("TRANSCRIPT_FLUSH_INTERVAL_S", "0")
    assert StoreConfig.from_env().flush_interval_s == 0.5  # never a busy loop

    monkeypatch.setenv("TRANSCRIPTION_HOOK_URL", "")
    assert StoreConfig.from_env() is None


@pytest.mark.parametrize("page_size", [1, 1000])
async def test_resume_reads_every_page(page_size):
    """All the parts are read whatever the listing page size."""
    fake_s3 = FakeS3(page_size=page_size)
    writer = make_store(fake_s3)
    await writer.open()
    for i in range(5):
        writer.add_segment(
            identity="u", name="u", start=T0 + i, end=T0 + i + 1, text=str(i)
        )
        await writer.flush()

    reader = make_store(fake_s3)
    await reader.open()

    assert [s.text for s in reader.segments] == ["0", "1", "2", "3", "4"]
    await writer.finalize()


V3 = ("gigaam-v3", "Русский — GigaAM v3")
PARAKEET = ("parakeet-v3", "Русский и английский — Parakeet v3")


async def test_recognition_models_in_the_final_files_and_hook(fake_s3):
    """Each segment keeps its model; the models used are listed once, in order."""
    hook = RecordingHook()
    store = make_store(fake_s3, hook=make_hook(hook))
    await store.open()
    for start, (model, label), text in [
        (T0 + 9, V3, "три"),
        (T0 + 1, PARAKEET, "раз"),
        (T0 + 5, V3, "два"),
        (T0 + 12, (None, None), "без модели"),
    ]:
        store.add_segment(
            identity="u1",
            name="Иван",
            start=start,
            end=start + 1,
            text=text,
            model=model,
            model_label=label,
        )

    assert await store.finalize()

    document = fake_s3.json(f"transcriptions/{TID}/transcript.json")
    assert document["models"] == [
        {"id": "parakeet-v3", "label": "Русский и английский — Parakeet v3"},
        {"id": "gigaam-v3", "label": "Русский — GigaAM v3"},
    ]
    assert [(s["text"], s.get("model")) for s in document["segments"]] == [
        ("раз", "parakeet-v3"),
        ("два", "gigaam-v3"),
        ("три", "gigaam-v3"),
        ("без модели", None),
    ]
    assert "model_label" not in document["segments"][0]
    assert "model" not in document["segments"][3]
    markdown = fake_s3.text(f"transcriptions/{TID}/transcript.md")
    assert markdown.splitlines()[2:5] == [
        "- Дата: 19.10.2026, 13:00–13:00 (Europe/Moscow)",
        "- Участники: Иван",
        (
            "- Модель распознавания: Русский и английский — Parakeet v3, "
            "Русский — GigaAM v3"
        ),
    ]
    (payload,) = hook.payloads
    assert payload["models"] == ["parakeet-v3", "gigaam-v3"]


async def test_model_labels_survive_a_restart(fake_s3):
    """The parts keep the model labels, so a resumed job still lists them."""
    previous = make_store(fake_s3)
    await previous.open()
    previous.add_segment(
        identity="u1",
        name="Иван",
        start=T0 + 1,
        end=T0 + 2,
        text="раз",
        model=V3[0],
        model_label=V3[1],
    )
    await previous.flush()
    (key,) = fake_s3.keys(f"transcriptions/{TID}/parts/")
    assert fake_s3.json(key)[0]["model"] == "gigaam-v3"
    assert fake_s3.json(key)[0]["model_label"] == "Русский — GigaAM v3"
    restarted = make_store(fake_s3, clock=Clock(T0 + 10))
    await restarted.open()
    restarted.add_segment(
        identity="u1", name="Иван", start=T0 + 11, end=T0 + 12, text="два", model="x"
    )

    assert await restarted.finalize()

    document = fake_s3.json(f"transcriptions/{TID}/transcript.json")
    # without a label (none answered), the id is shown
    assert document["models"] == [
        {"id": "gigaam-v3", "label": "Русский — GigaAM v3"},
        {"id": "x", "label": "x"},
    ]
    assert [s["text"] for s in document["segments"]] == ["раз", "два"]


def test_label_is_not_part_of_the_segment_identity():
    """The same phrase read with and without its label is one segment."""
    with_label = Segment("u1", "Иван", 1000, 2000, "раз", "gigaam-v3", "GigaAM v3")
    without_label = Segment("u1", "Иван", 1000, 2000, "раз", "gigaam-v3")
    other_model = Segment("u1", "Иван", 1000, 2000, "раз", "parakeet-v3")

    assert merge_segments([with_label, without_label]) == [with_label]
    assert len(merge_segments([with_label, other_model])) == 2
    assert Segment.from_json(with_label.to_part_json()).model_label == "GigaAM v3"
    assert Segment.from_json(with_label.to_json()) == with_label
    old_part = {"identity": "u1", "start": "2026-10-19T10:00:00Z"}
    old_part.update(end="2026-10-19T10:00:01Z", text="раз")
    assert Segment.from_json(old_part).model is None


def test_list_models_takes_the_first_known_label():
    """Distinct models in order of first phrase; a later label fills a gap."""
    segments = [
        Segment("u1", "a", 1, 2, "раз", "gigaam-v3"),
        Segment("u2", "b", 2, 3, "два", "parakeet-v3", "Parakeet v3"),
        Segment("u1", "a", 3, 4, "три", "gigaam-v3", "GigaAM v3"),
        Segment("u1", "a", 4, 5, "четыре", "gigaam-v3", "другое"),
        Segment("u1", "a", 5, 6, "пять"),
    ]

    assert list_models(segments) == [
        {"id": "gigaam-v3", "label": "GigaAM v3"},
        {"id": "parakeet-v3", "label": "Parakeet v3"},
    ]
    assert list_models([]) == []


def test_markdown_lists_the_models():
    """The header line lists the labels, escaped like the names."""
    markdown = render_markdown(
        room_name="Планёрка",
        started_ms=round(T0 * 1000),
        ended_ms=round(T0 * 1000) + 60_000,
        participants=[],
        segments=[],
        timezone="UTC",
        models=[{"id": "a", "label": "A*\n  B"}, {"id": "c", "label": "C"}],
    )

    assert markdown.endswith("- Участники: —\n- Модель распознавания: A\\* B, C\n")
