"""Tests for the parts that had been written but never run: the Redis Streams event bus and the
language-model providers for case summaries.

The bus runs against fakeredis, an emulator of Redis inside this process: the same client calls, without a
Redis server. The providers run against a stand-in HTTP transport: the same requests, without the network
and without an API key. Neither replaces a run against the real thing (docs/12, section 6).
"""
from __future__ import annotations

import asyncio
import json

import fakeredis
import httpx
import pytest

from app.bus import MemoryBus, RedisStreamBus
from app.workers import summariser


# ------------------------------------------------------------------ Redis Streams bus
async def _bus_run(n: int = 40):
    r = fakeredis.FakeAsyncRedis(decode_responses=True)
    bus = RedisStreamBus(r)
    seen: dict[str, list] = {"a": [], "b": [], "other": []}
    settles = []

    async def group_a(ev):
        seen["a"].append(ev["n"])

    async def group_b(ev):
        if ev["n"] == 3:
            raise RuntimeError("one bad event")
        seen["b"].append(ev["n"])

    async def other(ev):
        seen["other"].append(ev["n"])

    async def settle():
        settles.append(1)

    bus.subscribe("t.one", "a", group_a)
    bus.subscribe("t.one", "b", group_b)
    bus.subscribe("t.two", "o", other)
    bus.on_settle(settle)
    await bus.start()
    for i in range(n):
        await bus.publish("t.one", {"n": i})
    await bus.publish("t.two", {"n": 99})
    await bus.drain(5)
    pending = [await r.xpending("stream:t.one", g) for g in ("a", "b")]
    stats = (bus.published, bus.handled, bus.failed, await bus.pending())
    await bus.stop()
    return seen, pending, stats, len(settles)


def test_redis_bus_delivers_every_event_once_to_each_group_in_order():
    seen, pending, (published, handled, failed, left), settles = asyncio.run(_bus_run())
    assert seen["a"] == list(range(40))                                    # every event, in publish order
    assert seen["b"] == [i for i in range(40) if i != 3]                   # a failing handler loses only its event
    assert seen["other"] == [99]
    assert published == 41 and failed == 1 and handled == 40 + 39 + 1
    assert all(p["pending"] == 0 for p in pending) and left == 0          # everything acknowledged
    assert settles == handled                                              # buffered writes are flushed per event


def test_redis_bus_recovers_when_the_cache_is_emptied():
    async def run():
        r = fakeredis.FakeAsyncRedis(decode_responses=True)
        bus = RedisStreamBus(r)
        got = []

        async def h(ev):
            got.append(ev["n"])

        bus.subscribe("t", "g", h)
        await bus.start()
        await bus.publish("t", {"n": 1})
        await bus.drain(3)
        await r.flushdb()                       # takes the stream and its consumer group with it
        await asyncio.sleep(1.2)                # the consumer notices and recreates the group
        await bus.publish("t", {"n": 2})
        await bus.drain(3)
        await bus.stop()
        return got

    assert asyncio.run(run()) == [1, 2]


def test_redis_bus_does_not_spin_when_idle():
    async def run():
        r = fakeredis.FakeAsyncRedis(decode_responses=True)
        calls = 0
        real = r.xreadgroup

        async def counted(*a, **k):
            nonlocal calls
            calls += 1
            return await real(*a, **k)

        r.xreadgroup = counted
        bus = RedisStreamBus(r)

        async def h(ev):
            pass

        bus.subscribe("t", "g", h)
        await bus.start()
        await asyncio.sleep(1.0)
        await bus.stop()
        return calls

    assert asyncio.run(run()) < 40              # the emulator does not block; the consumer must pause by itself


def test_memory_bus_settles_between_topics_and_when_idle():
    async def run():
        bus = MemoryBus()
        log = []

        async def a(ev):
            log.append(("a", ev["n"]))

        async def b(ev):
            log.append(("b", ev["n"]))

        async def settle():
            log.append(("settle", None))

        bus.subscribe("one", "g", a)
        bus.subscribe("two", "g", b)
        bus.on_settle(settle)
        await bus.start()
        for n in (1, 2):
            await bus.publish("one", {"n": n})
        await bus.publish("two", {"n": 3})
        await bus.drain()
        await bus.stop()
        return log

    log = asyncio.run(run())
    assert log == [("a", 1), ("a", 2), ("settle", None), ("b", 3), ("settle", None)]


# ------------------------------------------------------------------ language-model providers
DATA = {"payer": "PAYER_1", "payee": "PAYEE_1", "amount_rupees": 180000.0, "tier": "BLOCK", "action": "HOLD", "score": 0.94,
        "reasons": ["You are on a call with a number that is not in your contacts."], "rules_fired": ["R-02"],
        "scam_family": "Digital arrest", "payee_account_age_days": 3, "linked_calls": [{"caller_risk_level": 3}],
        "linked_messages": [], "outcome": "HOLD_CANCELLED"}
GOOD = {"summary": "A payment of 180000 rupees from PAYER_1 to PAYEE_1 was held. The payer was on a call with an unknown number. "
                   "The payee account is 3 days old.",
        "key_evidence": ["Caller risk level 3", "Payee account age 3 days"], "suggested_action": "contact the customer"}


class _Settings:
    gemini_api_key = "test-gemini-key"
    gemini_model = "gemini-test"
    groq_api_key = "test-groq-key"
    groq_model = "groq-test"
    ollama_url = "http://ollama.test:11434"
    ollama_model = "llama-test"
    llm_providers = "gemini,groq,ollama,template"


def _install(monkeypatch, handler, settings=_Settings):
    """Route the summariser's HTTP calls to `handler` and record every request it makes."""
    requests: list[httpx.Request] = []
    real = httpx.AsyncClient

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    def client(**kw):
        return real(transport=httpx.MockTransport(record), **kw)

    monkeypatch.setattr(summariser.httpx, "AsyncClient", client)
    monkeypatch.setattr(summariser.rt, "settings", settings)
    return requests


def test_gemini_request_and_reply(monkeypatch):
    def handler(req):
        return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(GOOD)}]}}]})

    reqs = _install(monkeypatch, handler)
    text, model = asyncio.run(summariser._gemini("PROMPT"))
    assert json.loads(text) == GOOD and model == "gemini-test"
    req = reqs[0]
    assert req.url.host == "generativelanguage.googleapis.com" and "gemini-test:generateContent" in req.url.path
    assert req.headers["x-goog-api-key"] == "test-gemini-key" and "test-gemini-key" not in str(req.url)   # the key is not in the URL
    body = json.loads(req.content)
    assert body["contents"][0]["parts"][0]["text"] == "PROMPT" and body["generationConfig"]["responseMimeType"] == "application/json"


def test_groq_request_and_reply(monkeypatch):
    def handler(req):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(GOOD)}}]})

    reqs = _install(monkeypatch, handler)
    text, model = asyncio.run(summariser._groq("PROMPT"))
    assert json.loads(text) == GOOD and model == "groq-test"
    req = reqs[0]
    assert str(req.url) == "https://api.groq.com/openai/v1/chat/completions" and req.headers["authorization"] == "Bearer test-groq-key"
    body = json.loads(req.content)
    assert body["model"] == "groq-test" and body["messages"] == [{"role": "user", "content": "PROMPT"}]
    assert body["response_format"] == {"type": "json_object"}


def test_ollama_request_and_reply(monkeypatch):
    def handler(req):
        return httpx.Response(200, json={"response": json.dumps(GOOD)})

    reqs = _install(monkeypatch, handler)
    text, model = asyncio.run(summariser._ollama("PROMPT"))
    assert json.loads(text) == GOOD and model == "llama-test"
    assert str(reqs[0].url) == "http://ollama.test:11434/api/generate"
    assert json.loads(reqs[0].content) == {"model": "llama-test", "prompt": "PROMPT", "stream": False, "format": "json"}


def test_a_provider_without_a_key_is_skipped_without_a_request(monkeypatch):
    class NoKeys(_Settings):
        gemini_api_key = ""
        groq_api_key = ""
        ollama_url = ""

    reqs = _install(monkeypatch, lambda req: httpx.Response(500), NoKeys)
    assert asyncio.run(summariser._gemini("x")) is None and asyncio.run(summariser._groq("x")) is None
    assert asyncio.run(summariser._ollama("x")) is None and reqs == []


def test_replies_are_parsed_and_checked_against_the_input():
    assert summariser._parse("Here you go:\n```json\n" + json.dumps(GOOD) + "\n```") == GOOD
    assert summariser._parse("no json here") is None
    assert summariser._check(GOOD, DATA)
    invented = {**GOOD, "summary": GOOD["summary"] + " The payer lost 250000 rupees last month."}
    assert not summariser._check(invented, DATA)                           # a number that is not in the input
    assert not summariser._check({**GOOD, "summary": "PAYER_2 was also involved."}, DATA)      # a party that is not in the input
    assert not summariser._check({"summary": "x"}, DATA)                   # missing keys


def _generate(monkeypatch, handler, settings=_Settings):
    saved = []

    async def fake_input(decision_id):
        return dict(DATA)

    async def fake_execute(sql, *args):
        saved.append(args)

    reqs = _install(monkeypatch, handler, settings)
    monkeypatch.setattr(summariser, "build_input", fake_input)
    monkeypatch.setattr(summariser.db, "execute", fake_execute)
    result = asyncio.run(summariser.generate("11111111-1111-1111-1111-111111111111"))
    return result, reqs, saved


def test_the_first_provider_that_answers_properly_is_used(monkeypatch):
    def handler(req):
        if "googleapis" in req.url.host:
            return httpx.Response(429, json={"error": "rate limited"})                    # free tier exhausted
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(GOOD)}}]})

    result, reqs, saved = _generate(monkeypatch, handler)
    assert result["provider"] == "groq" and result["model"] == "groq-test"
    assert [r.url.host for r in reqs] == ["generativelanguage.googleapis.com", "api.groq.com"]   # ollama never asked
    assert "the payer" in result["summary"] and "PAYER_1" not in result["summary"]              # tokens become words
    assert saved and saved[0][4] == "groq"
    # only the tokenised facts leave the service: no name, no UPI ID, no phone number
    sent = reqs[1].content.decode()
    assert "PAYER_1" in sent and "@" not in json.loads(sent)["messages"][0]["content"]


def test_a_reply_that_invents_facts_is_rejected_and_the_template_is_used(monkeypatch):
    invented = {**GOOD, "summary": "The payer lost 999999 rupees."}

    def handler(req):
        if "googleapis" in req.url.host:
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps(invented)}]}}]})
        if "groq" in req.url.host:
            return httpx.Response(200, json={"choices": [{"message": {"content": "not json at all"}}]})
        raise httpx.ConnectError("ollama is not running")

    result, reqs, saved = _generate(monkeypatch, handler)
    assert result["provider"] == "template" and len(reqs) == 3
    assert "999999" not in result["summary"] and "Digital arrest" in result["summary"]
    assert saved[0][4] == "template"


@pytest.mark.parametrize("order,expected", [("ollama,template", "ollama"), ("template", "template"), ("nonsense,template", "template")])
def test_provider_order_comes_from_the_setting(monkeypatch, order, expected):
    class Custom(_Settings):
        llm_providers = order

    def handler(req):
        return httpx.Response(200, json={"response": json.dumps(GOOD)})

    result, _, _ = _generate(monkeypatch, handler, Custom)
    assert result["provider"] == expected
