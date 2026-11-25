"""Simulate the moment the sangeet toast starts: hundreds of guests + a burst of messages.

    python scripts/spike.py --url http://localhost:8080 --room demo-room --guests 200 --messages 60

Prints latency percentiles so you can show the before/after of NudgeBee's fix.
"""
from __future__ import annotations

import argparse
import asyncio
import random
import statistics
import time

import httpx
import websockets

LANGS = ["hi-IN", "ta-IN", "bn-IN", "te-IN", "kn-IN", "ml-IN", "mr-IN", "gu-IN", "pa-IN", "en-IN"]
LINES = [
    "Welcome everyone to the sangeet!",
    "Please gather near the stage, the couple is about to dance.",
    "Dinner is served in the garden.",
    "Baraat leaves at 7 PM sharp from the hotel gate.",
    "Happy Diwali to all our families!",
]


async def guest(ws_url: str, stop: asyncio.Event, received: list[int]) -> None:
    try:
        async with websockets.connect(f"{ws_url}?lang={random.choice(LANGS)}", open_timeout=20) as ws:
            while not stop.is_set():
                try:
                    await asyncio.wait_for(ws.recv(), 1)
                    received[0] += 1
                except asyncio.TimeoutError:
                    pass
    except Exception:
        pass


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--url", default="http://localhost:8080")
    p.add_argument("--room", default="demo-room")
    p.add_argument("--guests", type=int, default=150)
    p.add_argument("--messages", type=int, default=60)
    p.add_argument("--concurrency", type=int, default=20)
    a = p.parse_args()

    ws_url = a.url.replace("http", "ws", 1) + f"/ws/{a.room}"
    stop, received = asyncio.Event(), [0]
    guests = [asyncio.create_task(guest(ws_url, stop, received)) for _ in range(a.guests)]
    await asyncio.sleep(3)
    print(f"{a.guests} guests connected, firing {a.messages} messages ({a.concurrency} at a time)…")

    latencies: list[float] = []
    errors = 0
    sem = asyncio.Semaphore(a.concurrency)
    async with httpx.AsyncClient(base_url=a.url, timeout=60) as http:
        async def send(i: int) -> None:
            nonlocal errors
            async with sem:
                t = time.perf_counter()
                r = await http.post(f"/api/rooms/{a.room}/say", json={"text": random.choice(LINES), "speaker": f"Guest {i}"})
                if r.status_code == 200:
                    latencies.append(time.perf_counter() - t)
                else:
                    errors += 1
        await asyncio.gather(*(send(i) for i in range(a.messages)))

    await asyncio.sleep(2)
    stop.set()
    await asyncio.gather(*guests, return_exceptions=True)
    if latencies:
        q = statistics.quantiles(latencies, n=20)
        print(f"ok={len(latencies)} errors={errors} p50={statistics.median(latencies):.2f}s p95={q[18]:.2f}s max={max(latencies):.2f}s")
    print(f"captions delivered to guests: {received[0]}")


if __name__ == "__main__":
    asyncio.run(main())
