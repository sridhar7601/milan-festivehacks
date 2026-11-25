"""Sarvam AI client: Saaras (speech-to-text), Sarvam-Translate, Bulbul (text-to-speech).

Two implementations share one interface:
  * HttpSarvamClient - real REST calls (docs.sarvam.ai, api-subscription-key auth)
  * MockSarvamClient - offline stand-in for dev, tests and load tests

Both enforce a per-pod concurrency limit so a traffic spike queues (and shows up
in metrics) instead of overwhelming the upstream API.
"""
from __future__ import annotations

import asyncio
import base64
import io
import logging
import time
import wave
from abc import ABC, abstractmethod
from contextlib import asynccontextmanager
from dataclasses import dataclass

import httpx

from .config import Settings
from .metrics import SARVAM_ERRORS, SARVAM_LATENCY, SARVAM_QUEUE

log = logging.getLogger("milan.sarvam")


class SarvamError(RuntimeError):
    """Raised when a Sarvam call fails after retries."""


@dataclass(frozen=True)
class Transcript:
    text: str
    language_code: str | None


class SarvamClient(ABC):
    def __init__(self, max_concurrency: int) -> None:
        self._sem = asyncio.Semaphore(max_concurrency)

    @asynccontextmanager
    async def _slot(self, api: str):
        """Concurrency slot + latency/error metrics around one upstream call."""
        SARVAM_QUEUE.inc()
        start = time.perf_counter()
        try:
            async with self._sem:
                yield
        except Exception:
            SARVAM_ERRORS.labels(api=api).inc()
            raise
        finally:
            SARVAM_QUEUE.dec()
            SARVAM_LATENCY.labels(api=api).observe(time.perf_counter() - start)

    @abstractmethod
    async def transcribe(self, audio: bytes, filename: str, content_type: str) -> Transcript: ...

    @abstractmethod
    async def translate(self, text: str, source: str, target: str) -> str: ...

    @abstractmethod
    async def tts(self, text: str, language: str, speaker: str | None = None) -> str:
        """Return base64-encoded WAV audio."""

    async def aclose(self) -> None:  # pragma: no cover - overridden where needed
        return None


class HttpSarvamClient(SarvamClient):
    _RETRY_STATUS = {429, 500, 502, 503, 504}

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings.sarvam_max_concurrency)
        self._s = settings
        self._http = httpx.AsyncClient(
            base_url=settings.sarvam_base_url,
            headers={"api-subscription-key": settings.sarvam_api_key},
            timeout=settings.sarvam_timeout_s,
        )

    async def _post(self, api: str, path: str, **kwargs) -> dict:
        async with self._slot(api):
            last_exc: Exception | None = None
            for attempt in range(3):
                try:
                    resp = await self._http.post(path, **kwargs)
                    if resp.status_code in self._RETRY_STATUS and attempt < 2:
                        await asyncio.sleep(0.4 * (2**attempt))
                        continue
                    if resp.is_error:
                        raise SarvamError(f"{api} failed: HTTP {resp.status_code} {resp.text[:300]}")
                    return resp.json()
                except httpx.TransportError as exc:  # network blips are retryable
                    last_exc = exc
                    await asyncio.sleep(0.4 * (2**attempt))
            raise SarvamError(f"{api} failed after retries: {last_exc}")

    async def transcribe(self, audio: bytes, filename: str, content_type: str) -> Transcript:
        data = await self._post(
            "stt",
            "/speech-to-text",
            data={"model": self._s.sarvam_stt_model, "mode": "transcribe"},
            files={"file": (filename, audio, content_type)},
        )
        return Transcript(text=(data.get("transcript") or "").strip(), language_code=data.get("language_code"))

    async def translate(self, text: str, source: str, target: str) -> str:
        data = await self._post(
            "translate",
            "/translate",
            json={"input": text, "source_language_code": source or "auto", "target_language_code": target},
        )
        return data.get("translated_text", "")

    async def tts(self, text: str, language: str, speaker: str | None = None) -> str:
        payload: dict = {"text": text, "language_code": language, "model": self._s.sarvam_tts_model}
        if speaker:
            payload["speaker"] = speaker
        data = await self._post("tts", "/text-to-speech", json=payload)
        audios = data.get("audios") or []
        if not audios:
            raise SarvamError("tts returned no audio")
        # Bulbul may split long text into chunks; we return the first full WAV and
        # keep utterances short (<= MAX_TEXT_CHARS) so one chunk is the norm.
        return audios[0]

    async def aclose(self) -> None:
        await self._http.aclose()


def _silent_wav_b64(ms: int = 250, rate: int = 16000) -> str:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * int(rate * ms / 1000))
    return base64.b64encode(buf.getvalue()).decode()


class MockSarvamClient(SarvamClient):
    """Deterministic offline client. Latency is simulated inside the same
    concurrency slot so load tests reproduce real queueing behaviour."""

    _WAV = _silent_wav_b64()

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings.sarvam_max_concurrency)
        self._delay = settings.mock_latency_ms / 1000

    async def _work(self, api: str) -> None:
        async with self._slot(api):
            await asyncio.sleep(self._delay)

    async def transcribe(self, audio: bytes, filename: str, content_type: str) -> Transcript:
        await self._work("stt")
        return Transcript(text="नमस्ते सबको, आज की शाम बहुत खास है!", language_code="hi-IN")

    async def translate(self, text: str, source: str, target: str) -> str:
        await self._work("translate")
        return f"[{target}] {text}"

    async def tts(self, text: str, language: str, speaker: str | None = None) -> str:
        await self._work("tts")
        return self._WAV


def build_sarvam_client(settings: Settings) -> SarvamClient:
    if settings.use_mock_sarvam:
        log.warning("Sarvam running in MOCK mode (no SARVAM_API_KEY or MOCK_MODE=true)")
        return MockSarvamClient(settings)
    return HttpSarvamClient(settings)
