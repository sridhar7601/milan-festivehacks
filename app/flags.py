"""Feature flags via Flagsmith, with safe local defaults.

Flag contract (create these in Flagsmith with the same names):

| Feature              | Type          | Purpose                                                    |
|----------------------|---------------|------------------------------------------------------------|
| enabled_languages    | value (str)   | Comma list of language codes guests can pick               |
| voice_output         | boolean       | Bulbul TTS voice for listeners                             |
| rush_mode            | boolean       | Degrade to captions-only under load (flipped by NudgeBee)  |
| tts_voice            | value (str)   | Bulbul speaker; make it multivariate for an A/B test       |
| elder_mode           | boolean       | Large-text UI; target with segment trait role == "elder"   |

If Flagsmith is unreachable or not configured, defaults below apply, so the
event never breaks because of the flag service.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from .config import SUPPORTED_LANGUAGES, Settings

log = logging.getLogger("milan.flags")

DEFAULTS: dict[str, tuple[bool, object]] = {
    "enabled_languages": (True, "en-IN,hi-IN,ta-IN,bn-IN,te-IN,kn-IN,ml-IN,mr-IN,gu-IN,pa-IN"),
    "voice_output": (True, None),
    "rush_mode": (False, None),
    "tts_voice": (True, ""),
    "elder_mode": (False, None),
}


@dataclass(frozen=True)
class FlagState:
    enabled_languages: tuple[str, ...]
    voice_output: bool
    rush_mode: bool
    tts_voice: str | None
    elder_mode: bool

    @property
    def speak(self) -> bool:
        """Voice is produced only when enabled and not in rush mode."""
        return self.voice_output and not self.rush_mode

    def public(self) -> dict:
        return {
            "enabled_languages": list(self.enabled_languages),
            "voice_output": self.speak,
            "rush_mode": self.rush_mode,
            "elder_mode": self.elder_mode,
        }


def _parse_languages(raw: object) -> tuple[str, ...]:
    codes = [c.strip() for c in str(raw or "").split(",") if c.strip()]
    valid = tuple(c for c in codes if c in SUPPORTED_LANGUAGES)
    return valid or tuple(str(DEFAULTS["enabled_languages"][1]).split(","))


def _to_state(is_enabled, get_value) -> FlagState:
    langs_on = is_enabled("enabled_languages")
    voice = get_value("tts_voice") if is_enabled("tts_voice") else None
    return FlagState(
        enabled_languages=_parse_languages(get_value("enabled_languages") if langs_on else None),
        voice_output=bool(is_enabled("voice_output")),
        rush_mode=bool(is_enabled("rush_mode")),
        tts_voice=(str(voice).strip() or None) if voice else None,
        elder_mode=bool(is_enabled("elder_mode")),
    )


DEFAULT_STATE = _to_state(lambda n: DEFAULTS[n][0], lambda n: DEFAULTS[n][1])


class FlagService:
    """Thin async facade over the (blocking) Flagsmith SDK."""

    def __init__(self, settings: Settings) -> None:
        self._client = None
        if settings.use_flagsmith:
            from flagsmith import Flagsmith
            from flagsmith.models import DefaultFlag

            def _default(name: str) -> DefaultFlag:
                enabled, value = DEFAULTS.get(name, (False, None))
                return DefaultFlag(enabled=enabled, value=value)

            local = settings.flagsmith_env_key.startswith("ser.")
            self._client = Flagsmith(
                environment_key=settings.flagsmith_env_key,
                api_url=settings.flagsmith_api_url,
                # Server-side key => evaluate in-process, refresh every few seconds:
                # zero network calls on the hot path, NudgeBee flips land within seconds.
                enable_local_evaluation=local,
                environment_refresh_interval_seconds=settings.flagsmith_refresh_s,
                request_timeout_seconds=5,
                default_flag_handler=_default,
            )
            log.info("Flagsmith enabled (local_evaluation=%s)", local)
        else:
            log.warning("Flagsmith not configured - using built-in default flags")

    async def environment(self) -> FlagState:
        if self._client is None:
            return DEFAULT_STATE
        try:
            flags = await asyncio.to_thread(self._client.get_environment_flags)
            return _to_state(flags.is_feature_enabled, flags.get_feature_value)
        except Exception:  # never let the flag service take the event down
            log.exception("Flagsmith environment flags failed; using defaults")
            return DEFAULT_STATE

    async def for_guest(self, guest_id: str, traits: dict[str, str]) -> FlagState:
        """Identity flags enable segments (elder_mode) and A/B splits (tts_voice)."""
        if self._client is None:
            return DEFAULT_STATE
        try:
            flags = await asyncio.to_thread(self._client.get_identity_flags, guest_id, traits)
            return _to_state(flags.is_feature_enabled, flags.get_feature_value)
        except Exception:
            log.exception("Flagsmith identity flags failed; using defaults")
            return await self.environment()
