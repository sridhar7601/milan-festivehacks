import base64
import io
import wave

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("MOCK_MODE", "true")
    monkeypatch.setenv("MOCK_LATENCY_MS", "1")
    monkeypatch.setenv("FLAG_POLL_S", "0.1")
    monkeypatch.delenv("REDIS_URL", raising=False)
    from app.config import get_settings

    get_settings.cache_clear()
    from app.main import app

    with TestClient(app) as c:
        yield c
    get_settings.cache_clear()


def _wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000)
        w.writeframes(b"\x01\x00" * 8000)
    return buf.getvalue()


def _next(ws, type_):
    for _ in range(20):
        msg = ws.receive_json()
        if msg["type"] == type_:
            return msg
    raise AssertionError(f"no {type_} message")


def test_health_and_config(client):
    assert client.get("/healthz").json() == {"status": "ok"}
    cfg = client.get("/api/config").json()
    assert "ta-IN" in cfg["languages"] and cfg["mock"] is True
    assert client.get("/metrics").status_code == 200
    assert client.get("/stage/demo-room").status_code == 200
    assert client.get("/stage/x").status_code == 400


def test_text_is_translated_per_listener(client):
    with client.websocket_connect("/ws/sangeet-1?lang=ta-IN") as tamil, client.websocket_connect("/ws/sangeet-1?lang=pa-IN") as punjabi:
        assert _next(tamil, "welcome")["voice_output"] is True
        r = client.post("/api/rooms/sangeet-1/say", json={"text": "Welcome everyone", "speaker": "Arjun"})
        assert r.status_code == 200, r.text
        assert set(r.json()["languages"]) == {"ta-IN", "pa-IN"}
        ta, pa = _next(tamil, "caption"), _next(punjabi, "caption")
        assert ta["text"] == "[ta-IN] Welcome everyone" and ta["speaker"] == "Arjun"
        assert pa["text"] == "[pa-IN] Welcome everyone"
        base64.b64decode(ta["audio"])  # voice attached


def test_voice_utterance(client):
    with client.websocket_connect("/ws/diwali-call?lang=en-IN") as en:
        r = client.post("/api/rooms/diwali-call/speak", files={"audio": ("s.wav", _wav(), "audio/wav")}, data={"speaker": "Nani"})
        assert r.status_code == 200, r.text
        cap = _next(en, "caption")
        assert cap["text"].startswith("[en-IN]") and cap["source_lang"] == "hi-IN"


def test_same_language_not_translated(client):
    with client.websocket_connect("/ws/room-abc?lang=hi-IN") as hi:
        client.post("/api/rooms/room-abc/say", json={"text": "नमस्ते", "lang": "hi-IN"})
        assert _next(hi, "caption")["text"] == "नमस्ते"


def test_language_switch_updates_targets(client):
    with client.websocket_connect("/ws/room-xyz?lang=ta-IN") as ws:
        _next(ws, "welcome")
        ws.send_json({"type": "set_lang", "lang": "bn-IN"})
        _next(ws, "presence")
        _next(ws, "presence")
        r = client.post("/api/rooms/room-xyz/say", json={"text": "hi"})
        assert r.json()["languages"] == ["bn-IN"]


def test_validation(client):
    assert client.post("/api/rooms/x/say", json={"text": "hi"}).status_code == 400
    assert client.post("/api/rooms/good-room/say", json={"text": "a" * 2000}).status_code == 413
    assert client.post("/api/rooms/good-room/say", json={"text": "hi", "lang": "xx"}).status_code == 400
    r = client.post("/api/rooms/good-room/speak", files={"audio": ("a.txt", b"x", "text/plain")})
    assert r.status_code == 415


def test_rush_mode_skips_voice(client, monkeypatch):
    from app import flags as flags_mod

    rush = flags_mod._to_state(
        lambda n: True if n == "rush_mode" else flags_mod.DEFAULTS[n][0], lambda n: flags_mod.DEFAULTS[n][1]
    )

    async def fake_env(self):
        return rush

    monkeypatch.setattr(flags_mod.FlagService, "environment", fake_env)
    with client.websocket_connect("/ws/rush-room?lang=ta-IN") as ws:
        flags_msg = _next(ws, "flags")  # pushed by the poll loop
        assert flags_msg["rush_mode"] is True and flags_msg["voice_output"] is False
        r = client.post("/api/rooms/rush-room/say", json={"text": "hello"})
        assert r.json()["voiced"] == [] and r.json()["rush_mode"] is True
        cap = _next(ws, "caption")
        assert cap["audio"] is None and cap["text"] == "[ta-IN] hello"


@pytest.mark.asyncio
async def test_inmemory_broker_language_counts():
    from app.broker import InMemoryBroker

    b = InMemoryBroker()
    await b.add_listener("r", "ta-IN"); await b.add_listener("r", "ta-IN"); await b.add_listener("r", "hi-IN")
    await b.remove_listener("r", "ta-IN")
    assert await b.languages("r") == {"ta-IN", "hi-IN"}
    await b.remove_listener("r", "ta-IN"); await b.remove_listener("r", "hi-IN")
    assert await b.languages("r") == set()


@pytest.mark.asyncio
async def test_redis_broker_roundtrip():
    import asyncio

    import fakeredis.aioredis

    from app.broker import RedisBroker

    b = RedisBroker.__new__(RedisBroker)
    b._redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    await b.add_listener("r", "ta-IN")
    assert await b.languages("r") == {"ta-IN"}
    gen = b.listen()
    task = asyncio.create_task(gen.__anext__())
    await asyncio.sleep(0.1)
    await b.publish("r", {"type": "x", "v": "தமிழ்"})
    room, event = await asyncio.wait_for(task, 2)
    assert room == "r" and event["v"] == "தமிழ்"
    await gen.aclose()
    await b.remove_listener("r", "ta-IN")
    assert await b.languages("r") == set()
