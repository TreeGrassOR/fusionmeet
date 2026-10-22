"""Calls application configuration."""

from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class CallsConfig(AppConfig):
    """Configuration of the calls application."""

    name = "calls"
    verbose_name = _("Calls")
