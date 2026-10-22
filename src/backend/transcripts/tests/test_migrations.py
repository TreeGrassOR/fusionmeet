"""Test the data migrations of the transcripts application."""

import importlib

from django.apps import apps

import pytest

from core.factories import RoomFactory

from transcripts.models import RoomTranscriptionSettings

pytestmark = pytest.mark.django_db


def test_migration_0003_forgets_the_removed_model():
    """Rooms that chose parakeet-v3 go back to the default model, others keep theirs."""
    migration = importlib.import_module(
        "transcripts.migrations.0003_forget_parakeet_model"
    )
    removed = RoomTranscriptionSettings.objects.create(
        room=RoomFactory(), asr_model="parakeet-v3"
    )
    kept = RoomTranscriptionSettings.objects.create(
        room=RoomFactory(), asr_model="gigaam-v3"
    )

    migration.forget_removed_model(apps, None)

    removed.refresh_from_db()
    kept.refresh_from_db()
    assert removed.asr_model == ""
    assert kept.asr_model == "gigaam-v3"
