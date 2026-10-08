"""Event bus abstraction: one interface, three implementations (docs/04, section 2.3).

memory  in-process queue. Used when the API and the workers are one process (laptop).
redis   Redis Streams with consumer groups.
kafka   Kafka protocol (Redpanda locally).
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict
from typing import Any, Awaitable, Callable

log = logging.getLogger("bus")
Handler = Callable[[dict], Awaitable[None]]

TOPICS = [
    "txn.assessed", "txn.outcome", "call.events", "msg.checked", "fraud.reports",
    "reputation.updated", "guardian.events", "hold.events", "sim.transactions",
]


def _envelope(payload: dict) -> dict:
    payload.setdefault("event_id", uuid.uuid4().hex)
    payload.setdefault("occurred_at", time.time())
    return payload


class MemoryBus:
    """Single dispatcher task: events are handled in publish order, group by group."""

    def __init__(self) -> None:
        self.subs: dict[str, list[tuple[str, Handler]]] = defaultdict(list)
        self.queue: asyncio.Queue = asyncio.Queue()
        self.task: asyncio.Task | None = None
        self.published = 0
        self.failed = 0
        self.settlers: list[Callable[[], Awaitable[None]]] = []

    def subscribe(self, topic: str, group: str, handler: Handler) -> None:
        self.subs[topic].append((group, handler))

    def on_settle(self, fn: Callable[[], Awaitable[None]]) -> None:
        """A handler may buffer its writes. `fn` writes them out. It is called whenever the dispatcher has
        caught up, and before an event of a different topic is handled, so nothing ever reads stale state."""
        self.settlers.append(fn)

    async def _settle(self) -> None:
        for fn in self.settlers:
            try:
                await fn()
            except Exception:
                self.failed += 1
                log.exception("settle hook failed")

    async def publish(self, topic: str, payload: dict) -> None:
        self.published += 1
        self.queue.put_nowait((topic, _envelope(payload)))

    async def start(self) -> None:
        if self.task is None:
            self.task = asyncio.create_task(self._run(), name="bus-dispatcher")

    async def stop(self) -> None:
        if self.task:
            self.task.cancel()
            self.task = None

    async def drain(self) -> None:
        await self.queue.join()

    def backlog(self) -> int:
        return self.queue.qsize()

    async def _run(self) -> None:
        last = None
        while True:
            topic, payload = await self.queue.get()
            try:
                if last is not None and topic != last:
                    await self._settle()
                last = topic
                for group, handler in self.subs.get(topic, ()):
                    try:
                        await handler(payload)
                    except Exception:  # a failing consumer must not stop the others
                        self.failed += 1
                        log.exception("handler %s failed on %s", group, topic)
                if self.queue.empty():
                    await self._settle()
            finally:
                self.queue.task_done()


class RedisStreamBus:
    """Redis Streams with consumer groups: one stream per topic, one consumer task per (topic, group).

    Each group sees every event once and in order. Unlike the in-process bus, groups run side by side, so a
    handler must not assume that another group has already handled the same event.
    """

    STREAM = "stream:"           # warm-up keeps keys with this prefix when it rebuilds the cache
    BLOCK_MS = 2000
    IDLE_SLEEP_S = 0.05          # see _consume: a read that returns nothing at once must not become a busy loop
    CLAIM_IDLE_MS = 60_000       # an event delivered to a consumer that died is taken over after this long

    def __init__(self, redis: Any) -> None:
        self.r = redis
        self.subs: list[tuple[str, str, Handler]] = []
        self.tasks: list[asyncio.Task] = []
        self.published = 0
        self.failed = 0
        self.handled = 0
        self.settlers: list[Callable[[], Awaitable[None]]] = []
        self._busy = 0
        self._lag = 0

    def subscribe(self, topic: str, group: str, handler: Handler) -> None:
        self.subs.append((topic, group, handler))

    def on_settle(self, fn: Callable[[], Awaitable[None]]) -> None:
        self.settlers.append(fn)

    async def publish(self, topic: str, payload: dict) -> None:
        self.published += 1
        await self.r.xadd(f"{self.STREAM}{topic}", {"d": json.dumps(_envelope(payload), default=str)},
                          maxlen=10000, approximate=True)

    async def _ensure_group(self, topic: str, group: str) -> None:
        try:
            await self.r.xgroup_create(f"{self.STREAM}{topic}", group, id="$", mkstream=True)
        except Exception as exc:
            if "BUSYGROUP" not in str(exc):          # "already exists" is the normal case
                log.warning("could not create group %s on %s: %s", group, topic, exc)

    async def start(self) -> None:
        for topic, group, handler in self.subs:
            await self._ensure_group(topic, group)
            self.tasks.append(asyncio.create_task(self._consume(topic, group, handler), name=f"bus-{topic}-{group}"))
        self.tasks.append(asyncio.create_task(self._watch(), name="bus-lag"))

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        self.tasks.clear()

    async def pending(self) -> int:
        """Events published but not yet handled by every group that subscribes to them."""
        total = 0
        for topic, group in {(t, g) for t, g, _ in self.subs}:
            try:
                for info in await self.r.xinfo_groups(f"{self.STREAM}{topic}"):
                    if info.get("name") != group:
                        continue
                    lag = info.get("lag")
                    if lag is None:                  # older servers: count what lies after the group's position
                        last = info.get("last-delivered-id") or "0-0"
                        lag = len(await self.r.xrange(f"{self.STREAM}{topic}", min=f"({last}", max="+", count=1000))
                    total += int(lag or 0) + int(info.get("pending") or 0)
            except Exception:
                continue
        return total + self._busy

    async def drain(self, timeout: float = 10.0) -> None:
        """Wait until everything published so far has been handled (used by the demo reset and the tests)."""
        end = time.time() + timeout
        quiet = 0
        while time.time() < end:
            quiet = quiet + 1 if await self.pending() == 0 else 0
            if quiet >= 2:
                return
            await asyncio.sleep(0.05)

    def backlog(self) -> int:
        return self._lag

    async def _watch(self) -> None:
        while True:
            try:
                self._lag = await self.pending()
            except asyncio.CancelledError:
                raise
            except Exception:
                pass
            await asyncio.sleep(1.0)

    async def _handle(self, topic: str, group: str, handler: Handler, entry_id: str, fields: dict) -> None:
        self._busy += 1
        try:
            await handler(json.loads(fields["d"]))
            for fn in self.settlers:                 # no batching across messages on this bus
                await fn()
            self.handled += 1
        except Exception:                            # a failing consumer must not stop the others
            self.failed += 1
            log.exception("handler %s failed on %s", group, topic)
        finally:
            self._busy -= 1
        await self.r.xack(f"{self.STREAM}{topic}", group, entry_id)

    async def _consume(self, topic: str, group: str, handler: Handler) -> None:
        name = f"c-{uuid.uuid4().hex[:6]}"
        stream = f"{self.STREAM}{topic}"
        last_claim = 0.0
        while True:
            try:
                if time.time() - last_claim > 30:    # take over events a dead consumer was holding
                    last_claim = time.time()
                    try:
                        claimed = await self.r.xautoclaim(stream, group, name, min_idle_time=self.CLAIM_IDLE_MS,
                                                          start_id="0-0", count=50)
                        for entry_id, fields in (claimed[1] if claimed else []):
                            if fields:
                                await self._handle(topic, group, handler, entry_id, fields)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        pass                         # not every server (or emulator) has XAUTOCLAIM
                t0 = time.time()
                res = await self.r.xreadgroup(group, name, {stream: ">"}, count=50, block=self.BLOCK_MS)
            except asyncio.CancelledError:
                raise
            except Exception:
                # Most likely the cache was emptied (a warm-up, or a demo reset) and the stream and its group
                # went with it. Make sure the group exists and carry on, rather than failing every read from
                # now on. If the server is simply unreachable this fails too, and the loop tries again.
                try:
                    await self._ensure_group(topic, group)
                except Exception:
                    pass
                await asyncio.sleep(0.5)
                continue
            got = False
            for _stream, entries in res or []:
                for entry_id, fields in entries:
                    got = True
                    await self._handle(topic, group, handler, entry_id, fields)
            # A server that honours BLOCK waits up to two seconds for an event. One that does not (the
            # in-process emulator) answers at once with nothing, and without this pause the loop would spin.
            if not got and time.time() - t0 < 0.5:
                await asyncio.sleep(self.IDLE_SLEEP_S)


class KafkaBus:
    """Kafka protocol via aiokafka (pip install aiokafka). NOT TESTED: it has never been run against a broker.
    Kept as a sketch of the third implementation of the same interface; treat it as unfinished."""

    def __init__(self, brokers: str) -> None:
        self.brokers = brokers
        self.subs: list[tuple[str, str, Handler]] = []
        self.tasks: list[asyncio.Task] = []
        self.producer: Any = None
        self.published = 0
        self.failed = 0
        self.settlers: list[Callable[[], Awaitable[None]]] = []

    def subscribe(self, topic: str, group: str, handler: Handler) -> None:
        self.subs.append((topic, group, handler))

    def on_settle(self, fn: Callable[[], Awaitable[None]]) -> None:
        self.settlers.append(fn)

    async def publish(self, topic: str, payload: dict) -> None:
        self.published += 1
        await self.producer.send_and_wait(topic, json.dumps(_envelope(payload), default=str).encode())

    async def start(self) -> None:
        try:
            from aiokafka import AIOKafkaConsumer, AIOKafkaProducer
        except ImportError as exc:
            raise RuntimeError("EVENT_BUS=kafka needs the aiokafka package (pip install aiokafka). Note that this "
                               "bus has never been tested; use EVENT_BUS=memory or redis.") from exc

        self.producer = AIOKafkaProducer(bootstrap_servers=self.brokers)
        await self.producer.start()
        for topic, group, handler in self.subs:
            consumer = AIOKafkaConsumer(topic, bootstrap_servers=self.brokers, group_id=group,
                                        auto_offset_reset="latest")
            await consumer.start()
            self.tasks.append(asyncio.create_task(self._consume(consumer, group, handler)))

    async def stop(self) -> None:
        for t in self.tasks:
            t.cancel()
        if self.producer:
            await self.producer.stop()

    async def drain(self) -> None:
        await asyncio.sleep(0.5)

    def backlog(self) -> int:
        return 0

    async def _consume(self, consumer: Any, group: str, handler: Handler) -> None:
        try:
            async for msg in consumer:
                try:
                    await handler(json.loads(msg.value))
                except Exception:
                    self.failed += 1
                    log.exception("handler %s failed on %s", group, msg.topic)
        finally:
            await consumer.stop()


_bus: Any = None


def init_bus(kind: str, redis: Any = None, brokers: str = "") -> Any:
    global _bus
    if kind == "kafka":
        _bus = KafkaBus(brokers)
    elif kind == "redis":
        _bus = RedisStreamBus(redis)
    else:
        _bus = MemoryBus()
    return _bus


def bus() -> Any:
    if _bus is None:
        raise RuntimeError("event bus is not initialised")
    return _bus
