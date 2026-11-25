"""Milan - live multilingual rooms for India's festivals and weddings."""
from __future__ import annotations

import logging
import re
import uuid
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from pydantic import BaseModel, Field
from starlette.responses import Response

from .broker import build_broker
from .config import SUPPORTED_LANGUAGES, get_settings
from .flags import FlagService
from .hub import Hub, Listener
from .pipeline import EmptyUtteranceError, Pipeline
from .sarvam import SarvamError, build_sarvam_client

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("milan")

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
ROOM_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,39}$")
ALLOWED_AUDIO = {"audio/wav", "audio/x-wav", "audio/wave", "audio/webm", "audio/ogg", "audio/mpeg", "audio/mp4"}


def _room(room: str) -> str:
    room = room.lower()
    if not ROOM_RE.match(room):
        raise HTTPException(400, "Room id must be 3-40 chars: a-z, 0-9, '-'")
    return room


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    sarvam = build_sarvam_client(s)
    flags = FlagService(s)
    broker = build_broker(s.redis_url)
    hub = Hub(broker, flags, s.flag_poll_s)
    await hub.start()
    app.state.settings, app.state.flags, app.state.hub = s, flags, hub
    app.state.pipeline = Pipeline(sarvam, flags, broker)
    log.info("Milan started on pod %s (mock_sarvam=%s)", s.pod_name, s.use_mock_sarvam)
    try:
        yield
    finally:
        await hub.stop()
        await sarvam.aclose()
        await broker.aclose()


app = FastAPI(title="Milan", version="1.0.0", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


# ---------- pages ----------
@app.get("/", include_in_schema=False)
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/r/{room}", include_in_schema=False)
async def room_page(room: str) -> FileResponse:
    _room(room)
    return FileResponse(STATIC_DIR / "room.html")


@app.get("/stage/{room}", include_in_schema=False)
async def stage_page(room: str) -> FileResponse:
    _room(room)
    return FileResponse(STATIC_DIR / "stage.html")


# ---------- ops ----------
@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz() -> dict:
    hub: Hub = app.state.hub
    return {"status": "ready", "pod": app.state.settings.pod_name, **hub.stats()}


@app.get("/metrics", include_in_schema=False)
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


# ---------- API ----------
@app.get("/api/config")
async def api_config() -> dict:
    flags = app.state.hub.current_flags or await app.state.flags.environment()
    return {
        "languages": {c: SUPPORTED_LANGUAGES[c] for c in flags.enabled_languages},
        "flags": flags.public(),
        "pod": app.state.settings.pod_name,
        "mock": app.state.settings.use_mock_sarvam,
    }


class SayRequest(BaseModel):
    text: str = Field(min_length=1)
    speaker: str = Field(default="Guest", max_length=40)
    lang: str | None = None


def _result(r) -> dict:
    return asdict(r)


@app.post("/api/rooms/{room}/say")
async def say(room: str, body: SayRequest) -> dict:
    room = _room(room)
    s = app.state.settings
    if len(body.text) > s.max_text_chars:
        raise HTTPException(413, f"Keep it under {s.max_text_chars} characters")
    if body.lang and body.lang not in SUPPORTED_LANGUAGES:
        raise HTTPException(400, "Unsupported language")
    try:
        return _result(await app.state.pipeline.from_text(room, body.speaker.strip() or "Guest", body.text, body.lang))
    except EmptyUtteranceError as e:
        raise HTTPException(422, str(e))
    except SarvamError as e:
        log.error("say failed: %s", e)
        raise HTTPException(502, "Translation service is busy, please retry")


@app.post("/api/rooms/{room}/speak")
async def speak(room: str, audio: UploadFile = File(...), speaker: str = Form("Guest")) -> dict:
    room = _room(room)
    s = app.state.settings
    content_type = (audio.content_type or "").split(";")[0].strip()
    if content_type not in ALLOWED_AUDIO:
        raise HTTPException(415, f"Unsupported audio type: {content_type or 'unknown'}")
    data = await audio.read(s.max_audio_bytes + 1)
    if len(data) > s.max_audio_bytes:
        raise HTTPException(413, "Audio clip too long")
    if not data:
        raise HTTPException(422, "Empty audio")
    try:
        return _result(
            await app.state.pipeline.from_audio(
                room, speaker.strip()[:40] or "Guest", data, audio.filename or "speech.wav", content_type
            )
        )
    except EmptyUtteranceError as e:
        raise HTTPException(422, str(e))
    except SarvamError as e:
        log.error("speak failed: %s", e)
        raise HTTPException(502, "Speech service is busy, please retry")


# ---------- realtime ----------
@app.websocket("/ws/{room}")
async def ws_room(ws: WebSocket, room: str) -> None:
    room = room.lower()
    lang = ws.query_params.get("lang", "en-IN")
    if not ROOM_RE.match(room) or lang not in SUPPORTED_LANGUAGES:
        await ws.close(code=1008)
        return
    await ws.accept()
    hub: Hub = app.state.hub
    guest_id = (ws.query_params.get("guest") or uuid.uuid4().hex)[:64]
    role = ws.query_params.get("role", "guest")[:20]
    name = (ws.query_params.get("name") or "Guest")[:40]

    guest_flags = await app.state.flags.for_guest(guest_id, {"role": role, "language": lang, "room": room})
    listener = Listener(ws=ws, room=room, guest_id=guest_id, name=name, lang=lang, elder_mode=guest_flags.elder_mode)
    await hub.join(listener)
    env = hub.current_flags or guest_flags
    await listener.send({"type": "welcome", "guest": guest_id, **env.public(), "elder_mode": guest_flags.elder_mode})
    try:
        while True:
            msg = await ws.receive_json()
            if msg.get("type") == "set_lang" and msg.get("lang") in SUPPORTED_LANGUAGES:
                await hub.change_language(listener, msg["lang"])
            elif msg.get("type") == "ping":
                await listener.send({"type": "pong"})
    except (WebSocketDisconnect, RuntimeError, ValueError):
        pass
    finally:
        await hub.leave(listener)
