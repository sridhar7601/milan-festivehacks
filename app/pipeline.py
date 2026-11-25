"""Utterance pipeline: speech/text -> Saaras STT -> Sarvam-Translate -> Bulbul TTS -> broker.

Design choices:
* Translate only into languages someone in the room is actually listening in
  (and that Flagsmith allows) - no wasted Sarvam calls.
* Every per-language step is isolated: one failed translation or voice never
  blocks the others; listeners fall back to the original text.
* Rush mode (Flagsmith) skips TTS entirely - the cheapest, fastest path.
"""
from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass

from .broker import Broker
from .flags import FlagService
from .metrics import PIPELINE_LATENCY, UTTERANCES
from .sarvam import SarvamClient

log = logging.getLogger("milan.pipeline")


@dataclass(frozen=True)
class UtteranceResult:
    id: str
    source_text: str
    source_lang: str
    languages: list[str]
    voiced: list[str]
    rush_mode: bool
    seconds: float


class EmptyUtteranceError(ValueError):
    """Speech-to-text produced nothing usable."""


class Pipeline:
    def __init__(self, sarvam: SarvamClient, flags: FlagService, broker: Broker) -> None:
        self._sarvam = sarvam
        self._flags = flags
        self._broker = broker

    async def from_audio(self, room: str, speaker: str, audio: bytes, filename: str, content_type: str) -> UtteranceResult:
        start = time.perf_counter()
        transcript = await self._sarvam.transcribe(audio, filename, content_type)
        if not transcript.text:
            raise EmptyUtteranceError("Could not hear any speech - try again closer to the mic")
        UTTERANCES.labels(kind="voice").inc()
        return await self._fan_out(room, speaker, transcript.text, transcript.language_code or "auto", start)

    async def from_text(self, room: str, speaker: str, text: str, lang: str | None) -> UtteranceResult:
        start = time.perf_counter()
        text = text.strip()
        if not text:
            raise EmptyUtteranceError("Message is empty")
        UTTERANCES.labels(kind="text").inc()
        return await self._fan_out(room, speaker, text, lang or "auto", start)

    async def _translate(self, text: str, source: str, target: str) -> tuple[str, bool]:
        if source == target:
            return text, True
        try:
            return await self._sarvam.translate(text, source, target), True
        except Exception:
            log.exception("translate %s->%s failed", source, target)
            return text, False

    async def _voice(self, text: str, lang: str, speaker: str | None) -> str | None:
        try:
            return await self._sarvam.tts(text, lang, speaker)
        except Exception:
            log.exception("tts %s failed", lang)
            return None

    async def _fan_out(self, room: str, speaker: str, text: str, source: str, start: float) -> UtteranceResult:
        env_flags, room_flags, listening = await asyncio.gather(
            self._flags.environment(),
            # Room is the A/B unit for the voice experiment (one voice per event).
            self._flags.for_guest(f"room-{room}", {"type": "room"}),
            self._broker.languages(room),
        )
        targets = sorted(listening & set(env_flags.enabled_languages))

        results = await asyncio.gather(*(self._translate(text, source, t) for t in targets))
        translations = {t: r[0] for t, r in zip(targets, results)}
        failed = [t for t, r in zip(targets, results) if not r[1]]

        audio: dict[str, str] = {}
        if env_flags.speak:
            voices = await asyncio.gather(
                *(self._voice(translations[t], t, room_flags.tts_voice) for t in targets if t not in failed)
            )
            audio = {t: v for t, v in zip([t for t in targets if t not in failed], voices) if v}

        event_id = uuid.uuid4().hex[:12]
        await self._broker.publish(
            room,
            {
                "type": "utterance",
                "id": event_id,
                "speaker": speaker,
                "source_lang": source,
                "source_text": text,
                "translations": translations,
                "untranslated": failed,
                "audio": audio,
                "rush_mode": env_flags.rush_mode,
                "ts": time.time(),
            },
        )
        seconds = time.perf_counter() - start
        PIPELINE_LATENCY.observe(seconds)
        return UtteranceResult(
            id=event_id,
            source_text=text,
            source_lang=source,
            languages=targets,
            voiced=sorted(audio),
            rush_mode=env_flags.rush_mode,
            seconds=round(seconds, 3),
        )
