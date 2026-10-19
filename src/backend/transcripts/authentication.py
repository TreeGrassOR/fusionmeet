"""Authentication of the transcriber agent calling the backend."""

from core.recording.event.authentication import HeaderBasedAuthentication


class TranscriptionAgentAuthentication(HeaderBasedAuthentication):
    """Authenticate the transcriber agent with a shared Bearer token.

    The token is compared in constant time with `TRANSCRIPTION_AGENT_TOKEN`.
    """

    REALM = "Transcription agent hook API"
    EXPECTED_TOKEN_SETTINGS_KEY = "TRANSCRIPTION_AGENT_TOKEN"  # noqa: S105
