"""Central, typed configuration loaded once from environment variables.

Every knob lives here so the app, tests and Kubernetes manifests share one contract.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache

# Languages Sarvam supports for translation + TTS (BCP-47 codes Sarvam uses).
SUPPORTED_LANGUAGES: dict[str, str] = {
    "en-IN": "English",
    "hi-IN": "हिन्दी (Hindi)",
    "bn-IN": "বাংলা (Bengali)",
    "ta-IN": "தமிழ் (Tamil)",
    "te-IN": "తెలుగు (Telugu)",
    "kn-IN": "ಕನ್ನಡ (Kannada)",
    "ml-IN": "മലയാളം (Malayalam)",
    "mr-IN": "मराठी (Marathi)",
    "gu-IN": "ગુજરાતી (Gujarati)",
    "pa-IN": "ਪੰਜਾਬੀ (Punjabi)",
    "od-IN": "ଓଡ଼ିଆ (Odia)",
}


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw not in (None, "") else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class Settings:
    # --- Sarvam AI ---
    sarvam_api_key: str = ""
    sarvam_base_url: str = "https://api.sarvam.ai"
    sarvam_stt_model: str = "saaras:v4"
    sarvam_tts_model: str = "bulbul:v4-flash"
    sarvam_timeout_s: float = 20.0
    sarvam_max_concurrency: int = 8  # back-pressure guard per pod

    # --- Flagsmith ---
    flagsmith_env_key: str = ""  # server-side key ("ser.…") enables local evaluation
    flagsmith_api_url: str = "https://edge.api.flagsmith.com/api/v1/"
    flagsmith_refresh_s: float = 5.0

    # --- Runtime ---
    redis_url: str = ""  # empty => single-pod in-memory broker
    mock_mode: bool = False  # true => no external calls (dev, tests, offline demo)
    mock_latency_ms: int = 300
    flag_poll_s: float = 3.0
    max_audio_bytes: int = 5 * 1024 * 1024
    max_text_chars: int = 1000
    pod_name: str = field(default_factory=lambda: os.getenv("HOSTNAME", "local"))

    @property
    def use_mock_sarvam(self) -> bool:
        return self.mock_mode or not self.sarvam_api_key

    @property
    def use_flagsmith(self) -> bool:
        return bool(self.flagsmith_env_key) and not self.mock_mode


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        sarvam_api_key=os.getenv("SARVAM_API_KEY", ""),
        sarvam_base_url=os.getenv("SARVAM_BASE_URL", "https://api.sarvam.ai"),
        sarvam_stt_model=os.getenv("SARVAM_STT_MODEL", "saaras:v4"),
        sarvam_tts_model=os.getenv("SARVAM_TTS_MODEL", "bulbul:v4-flash"),
        sarvam_timeout_s=_env_float("SARVAM_TIMEOUT_S", 20.0),
        sarvam_max_concurrency=_env_int("SARVAM_MAX_CONCURRENCY", 8),
        flagsmith_env_key=os.getenv("FLAGSMITH_ENVIRONMENT_KEY", ""),
        flagsmith_api_url=os.getenv("FLAGSMITH_API_URL", "https://edge.api.flagsmith.com/api/v1/"),
        flagsmith_refresh_s=_env_float("FLAGSMITH_REFRESH_S", 5.0),
        redis_url=os.getenv("REDIS_URL", ""),
        mock_mode=_env_bool("MOCK_MODE", False),
        mock_latency_ms=_env_int("MOCK_LATENCY_MS", 300),
        flag_poll_s=_env_float("FLAG_POLL_S", 3.0),
        max_audio_bytes=_env_int("MAX_AUDIO_BYTES", 5 * 1024 * 1024),
        max_text_chars=_env_int("MAX_TEXT_CHARS", 1000),
    )
