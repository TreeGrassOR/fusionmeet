"""Forget the recognition model parakeet-v3, removed from the ASR service.

Rooms that chose it use the default model again (empty `asr_model`), instead of
showing "the chosen model is unavailable" and sending a model the ASR service
refuses.
"""

from django.db import migrations

REMOVED_MODEL = "parakeet-v3"


def forget_removed_model(apps, schema_editor):
    """Reset the rooms that chose the removed model to the default model."""
    settings_model = apps.get_model("transcripts", "RoomTranscriptionSettings")
    settings_model.objects.filter(asr_model=REMOVED_MODEL).update(asr_model="")


class Migration(migrations.Migration):
    dependencies = [
        ("transcripts", "0002_room_transcription_settings"),
    ]

    operations = [
        migrations.RunPython(forget_removed_model, migrations.RunPython.noop),
    ]
