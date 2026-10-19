"""Factories of the transcripts application, for tests."""

import factory.django

from core.factories import RoomFactory

from . import models


class TranscriptFactory(factory.django.DjangoModelFactory):
    """Create fake transcripts for testing."""

    class Meta:
        model = models.Transcript
        skip_postgeneration_save = True

    room = factory.SubFactory(RoomFactory)
    status = models.TranscriptStatusChoices.ACTIVE

    @factory.post_generation
    def users(self, create, extracted, **kwargs):
        """Give the listed users access to the transcript."""
        if create and extracted:
            self.users.add(*extracted)
