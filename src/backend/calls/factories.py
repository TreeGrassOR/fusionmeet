"""Factories of the calls application, for tests."""

import uuid
from datetime import timedelta

from django.utils import timezone

import factory.django

from core.factories import RoomFactory

from . import models


class CallSessionFactory(factory.django.DjangoModelFactory):
    """Create fake calls for testing."""

    class Meta:
        model = models.CallSession

    room = factory.SubFactory(RoomFactory)
    livekit_sid = factory.Sequence(lambda n: f"RM_test{n}")
    # Whole seconds, like the times of LiveKit events
    started_at = factory.LazyFunction(
        lambda: timezone.now().replace(microsecond=0) - timedelta(hours=1)
    )
    last_activity_at = factory.LazyAttribute(lambda o: o.started_at)


class CallParticipantFactory(factory.django.DjangoModelFactory):
    """Create fake call participants for testing."""

    class Meta:
        model = models.CallParticipant

    session = factory.SubFactory(CallSessionFactory)
    identity = factory.LazyAttribute(
        lambda o: o.user.sub if o.user else str(uuid.uuid4())
    )
    user = None
    name = factory.Faker("name")
    is_guest = factory.LazyAttribute(lambda o: o.user is None)
    joined_at = factory.LazyAttribute(lambda o: o.session.started_at)
    last_joined_at = factory.LazyAttribute(lambda o: o.joined_at)


class ChatMessageFactory(factory.django.DjangoModelFactory):
    """Create fake chat messages for testing."""

    class Meta:
        model = models.ChatMessage

    session = factory.SubFactory(CallSessionFactory)
    client_id = factory.Faker("uuid4")
    sender_identity = factory.LazyAttribute(
        lambda o: o.sender_user.sub if o.sender_user else "guest-identity"
    )
    sender_user = None
    sender_name = factory.Faker("first_name")
    text = factory.Faker("sentence")
    sent_at = factory.LazyAttribute(lambda o: o.session.started_at)
