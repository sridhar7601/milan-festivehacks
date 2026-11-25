"""Per-pod WebSocket hub: tracks local listeners and delivers broker events to them."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field

from fastapi import WebSocket

from .broker import Broker
from .flags import FlagService, FlagState
from .metrics import DEGRADED, WS_CONNECTIONS

log = logging.getLogger("milan.hub")

SEND_TIMEOUT_S = 5.0


@dataclass(eq=False)
class Listener:
    ws: WebSocket
    room: str
    guest_id: str
    name: str
    lang: str
    elder_mode: bool = False
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def send(self, payload: dict) -> bool:
        try:
            async with self.lock:  # Starlette WebSockets are not safe for concurrent sends
                await asyncio.wait_for(self.ws.send_json(payload), SEND_TIMEOUT_S)
            return True
        except Exception:
            return False


def personalise(event: dict, lang: str) -> dict:
    """Shape one broker event for one listener (their language only - small payload)."""
    if event.get("type") != "utterance":
        return event
    text = event["translations"].get(lang, event["source_text"])
    return {
        "type": "caption",
        "id": event["id"],
        "speaker": event["speaker"],
        "text": text,
        "original": event["source_text"],
        "source_lang": event["source_lang"],
        "translated": lang in event["translations"] and lang not in event.get("untranslated", []),
        "audio": event["audio"].get(lang),
        "rush_mode": event.get("rush_mode", False),
        "ts": event["ts"],
    }


class Hub:
    def __init__(self, broker: Broker, flags: FlagService, poll_s: float) -> None:
        self._broker = broker
        self._flags = flags
        self._poll_s = poll_s
        self._rooms: dict[str, set[Listener]] = {}
        self._tasks: list[asyncio.Task] = []
        self.current_flags: FlagState | None = None

    # ---- lifecycle -------------------------------------------------------
    async def start(self) -> None:
        self.current_flags = await self._flags.environment()
        DEGRADED.set(1 if self.current_flags.rush_mode else 0)
        self._tasks = [asyncio.create_task(self._deliver_loop()), asyncio.create_task(self._flag_loop())]

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    # ---- membership ------------------------------------------------------
    async def join(self, listener: Listener) -> None:
        self._rooms.setdefault(listener.room, set()).add(listener)
        await self._broker.add_listener(listener.room, listener.lang)
        WS_CONNECTIONS.inc()
        await self._broadcast_presence(listener.room)

    async def leave(self, listener: Listener) -> None:
        members = self._rooms.get(listener.room)
        if not members or listener not in members:
            return
        members.discard(listener)
        if not members:
            self._rooms.pop(listener.room, None)
        await self._broker.remove_listener(listener.room, listener.lang)
        WS_CONNECTIONS.dec()
        await self._broadcast_presence(listener.room)

    async def change_language(self, listener: Listener, lang: str) -> None:
        if lang == listener.lang:
            return
        await self._broker.add_listener(listener.room, lang)
        await self._broker.remove_listener(listener.room, listener.lang)
        listener.lang = lang
        await self._broadcast_presence(listener.room)

    async def _broadcast_presence(self, room: str) -> None:
        langs = sorted(await self._broker.languages(room))
        await self._broker.publish(room, {"type": "presence", "languages": langs})

    # ---- delivery --------------------------------------------------------
    async def _send_all(self, room: str, make_payload) -> None:
        members = list(self._rooms.get(room, ()))
        if not members:
            return
        results = await asyncio.gather(*(m.send(make_payload(m)) for m in members))
        for m, ok in zip(members, results):
            if not ok:  # dead socket: clean up so counts stay accurate
                await self.leave(m)

    async def _deliver_loop(self) -> None:
        while True:
            try:
                async for room, event in self._broker.listen():
                    await self._send_all(room, lambda m, e=event: personalise(e, m.lang))
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Delivery loop crashed; restarting")
                await asyncio.sleep(0.5)

    async def _flag_loop(self) -> None:
        """Push flag changes (e.g. NudgeBee turning on rush mode) to every screen."""
        while True:
            await asyncio.sleep(self._poll_s)
            try:
                state = await self._flags.environment()
                if state != self.current_flags:
                    self.current_flags = state
                    DEGRADED.set(1 if state.rush_mode else 0)
                    log.info("Flags changed: %s", state.public())
                    payload = {"type": "flags", **state.public()}
                    for room in list(self._rooms):
                        await self._send_all(room, lambda m: {**payload, "elder_mode": m.elder_mode})
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Flag poll failed")

    def stats(self) -> dict:
        return {"rooms": len(self._rooms), "listeners": sum(len(v) for v in self._rooms.values())}
