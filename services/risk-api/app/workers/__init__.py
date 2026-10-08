"""Workers: subscribe to the event bus and start the timed jobs.

They run inside the API process (WORKERS_IN_PROCESS=true, the only supported setup). Running them as a
separate process is not built: besides an entry point it needs the real-time layer to publish through Redis,
because the sockets are held by the API process (docs/12, section 6).
"""
from __future__ import annotations

import asyncio

from ..bus import bus
from . import jobs, pipeline, protect, summariser

_tasks: list[asyncio.Task] = []


def subscribe() -> None:
    b = bus()
    # order matters on the in-process bus: the logger persists before anything reads it back
    b.subscribe("txn.assessed", "outcome-logger", pipeline.log_assessed)
    b.on_settle(pipeline.flush_assessed)       # decisions are written in batches; see pipeline.flush_assessed
    b.subscribe("txn.assessed", "summariser", summariser.on_assessed)
    b.subscribe("txn.outcome", "outcome-logger", pipeline.log_outcome)
    b.subscribe("txn.outcome", "reputation", pipeline.rep_on_outcome)
    b.subscribe("fraud.reports", "reputation", pipeline.rep_on_report)
    b.subscribe("msg.checked", "reputation", pipeline.rep_on_message)
    b.subscribe("reputation.updated", "consortium", pipeline.consortium_on_reputation)
    b.subscribe("reputation.updated", "dashboard", pipeline.notify_reputation)
    b.subscribe("guardian.events", "guardian", protect.on_guardian_event)
    b.subscribe("hold.events", "holds", protect.on_hold_event)


def start_loops() -> None:
    for coro, name in ((protect.scheduler_loop(), "scheduler"), (jobs.graph_loop(), "graph"),
                       (jobs.monitor_loop(), "monitor"), (summariser.summariser_loop(), "summariser")):
        _tasks.append(asyncio.create_task(coro, name=name))


async def stop_loops() -> None:
    for t in _tasks:
        t.cancel()
    _tasks.clear()
    jobs.stop_pool()
