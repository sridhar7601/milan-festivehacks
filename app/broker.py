"""Cross-pod fan-out and shared room state.

Kubernetes scales Milan horizontally (NudgeBee does it during a rush), so a
speaker and a listener may be connected to different pods. Every event is
published to the broker and each pod delivers it to its own WebSocket clients.

* InMemoryBroker - single process (local dev, tests)
* RedisBroker    - Redis pub/sub + hashes (any replica count)
"""
from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from collections import Counter, defaultdict
from typing import AsyncIterator

log = logging.getLogger("milan.broker")

CHANNEL_PREFIX = "milan:room:"


class Broker(ABC):
    @abstractmethod
    async def publish(self, room: str, event: dict) -> None: ...

    @abstractmethod
    def listen(self) -> AsyncIterator[tuple[str, dict]]:
        """Yield (room, event) for every room, forever."""

    @abstractmethod
    async def add_listener(self, room: str, lang: str) -> None: ...

    @abstractmethod
    async def remove_listener(self, room: str, lang: str) -> None: ...

    @abstractmethod
    async def languages(self, room: str) -> set[str]:
        """Languages that at least one listener in the room (any pod) needs."""

    async def aclose(self) -> None:
        return None


class InMemoryBroker(Broker):
    def __init__(self) -> None:
        self._subs: list[asyncio.Queue] = []
        self._langs: dict[str, Counter] = defaultdict(Counter)

    async def publish(self, room: str, event: dict) -> None:
        for q in list(self._subs):
            q.put_nowait((room, event))

    async def listen(self) -> AsyncIterator[tuple[str, dict]]:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.append(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._subs.remove(q)

    async def add_listener(self, room: str, lang: str) -> None:
        self._langs[room][lang] += 1

    async def remove_listener(self, room: str, lang: str) -> None:
        counts = self._langs.get(room)
        if not counts:
            return
        counts[lang] -= 1
        if counts[lang] <= 0:
            del counts[lang]
        if not counts:
            self._langs.pop(room, None)

    async def languages(self, room: str) -> set[str]:
        return set(self._langs.get(room, {}))


class RedisBroker(Broker):
    LANG_TTL_S = 6 * 60 * 60  # stale counts from crashed pods expire after the event

    def __init__(self, url: str) -> None:
        import redis.asyncio as redis

        self._redis = redis.from_url(url, decode_responses=True)

    @staticmethod
    def _langs_key(room: str) -> str:
        return f"milan:langs:{room}"

    async def publish(self, room: str, event: dict) -> None:
        await self._redis.publish(CHANNEL_PREFIX + room, json.dumps(event, ensure_ascii=False))

    async def listen(self) -> AsyncIterator[tuple[str, dict]]:
        while True:  # reconnect loop: a Redis blip must not kill delivery
            pubsub = self._redis.pubsub()
            try:
                await pubsub.psubscribe(CHANNEL_PREFIX + "*")
                async for msg in pubsub.listen():
                    if msg.get("type") != "pmessage":
                        continue
                    room = msg["channel"][len(CHANNEL_PREFIX):]
                    try:
                        yield room, json.loads(msg["data"])
                    except json.JSONDecodeError:
                        log.warning("Dropping malformed event on %s", msg["channel"])
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Redis subscription lost; reconnecting in 1s")
                await asyncio.sleep(1)
            finally:
                try:
                    await pubsub.aclose()
                except Exception:
                    pass

    async def add_listener(self, room: str, lang: str) -> None:
        key = self._langs_key(room)
        async with self._redis.pipeline(transaction=True) as p:
            p.hincrby(key, lang, 1)
            p.expire(key, self.LANG_TTL_S)
            await p.execute()

    async def remove_listener(self, room: str, lang: str) -> None:
        key = self._langs_key(room)
        remaining = await self._redis.hincrby(key, lang, -1)
        if remaining <= 0:
            await self._redis.hdel(key, lang)

    async def languages(self, room: str) -> set[str]:
        data = await self._redis.hgetall(self._langs_key(room))
        return {lang for lang, n in data.items() if int(n) > 0}

    async def aclose(self) -> None:
        await self._redis.aclose()


def build_broker(redis_url: str) -> Broker:
    if redis_url:
        log.info("Using Redis broker")
        return RedisBroker(redis_url)
    log.warning("Using in-memory broker (single replica only)")
    return InMemoryBroker()
