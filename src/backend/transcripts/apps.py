"""Transcripts application configuration."""

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class TranscriptsConfig(AppConfig):
    """Configuration of the transcripts application."""

    name = "transcripts"
    verbose_name = _("Transcripts")
