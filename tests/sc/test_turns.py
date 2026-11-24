"""Turn engine against a scripted SSE server: deadlines, cancel, pre-emption."""

from __future__ import annotations

import asyncio
import time

import pytest

from sc.llm import LLMCancelled, LLMClient, LLMTimeout, LLMUnavailable
from sc.turns import Limits, Turn, TurnEngine, TurnPreempted, TurnRejected

MSGS = [{"role": "user", "content": "hi"}]


def engine_for(url: str, **over) -> TurnEngine:
    voice = Limits(queue_s=over.get("vq", 0.5), first_token_s=over.get("vft", 2.0),
                   total_s=over.get("vt", 5.0), max_tokens=64)
    chat = Limits(queue_s=over.get("cq", 3.0), first_token_s=over.get("cft", 2.0),
                  total_s=over.get("ct", 5.0), max_tokens=64)
    return TurnEngine(LLMClient(url, "fake"), {"voice": voice, "chat": chat}, temperature=0.5)


async def collect(engine, turn):
    return [t async for t in engine.run(turn, MSGS)]


async def test_streams_tokens_and_completes(fake_llm):
    eng = engine_for(fake_llm.url)
    turn = Turn(kind="chat", user="jack")
    assert await collect(eng, turn) == ["Hello", " there", "."]
    assert turn.status == "completed" and turn.stats.tokens == 3
    assert fake_llm.requests[0]["stream"] is True and fake_llm.requests[0]["cache_prompt"] is True
    await eng.llm.close()


async def test_only_one_generation_at_a_time(fake_llm):
    fake_llm.script.token_delay = 0.05
    eng = engine_for(fake_llm.url)
    turns = [Turn(kind="chat", user="jack") for _ in range(3)]
    await asyncio.gather(*(collect(eng, t) for t in turns))
    assert fake_llm.max_active == 1
    assert all(t.status == "completed" for t in turns)
    await eng.llm.close()


async def test_first_token_deadline(fake_llm):
    fake_llm.script.first_delay = 1.5
    eng = engine_for(fake_llm.url, cft=0.4)
    turn = Turn(kind="chat", user="jack")
    t0 = time.monotonic()
    with pytest.raises(LLMTimeout):
        await collect(eng, turn)
    assert time.monotonic() - t0 < 1.2
    assert eng.active is None, "slot must be released after a timeout"
    await eng.llm.close()


async def test_total_deadline(fake_llm):
    fake_llm.script.tokens = ["x"] * 50
    fake_llm.script.token_delay = 0.1
    eng = engine_for(fake_llm.url, ct=0.6)
    with pytest.raises(LLMTimeout):
        await collect(eng, Turn(kind="chat", user="jack"))
    await asyncio.sleep(0.3)
    assert fake_llm.disconnects == 1, "timed-out stream must be closed so the server stops"
    await eng.llm.close()


async def test_cancel_mid_stream_closes_server_stream(fake_llm):
    fake_llm.script.tokens = ["x"] * 50
    fake_llm.script.token_delay = 0.05
    eng = engine_for(fake_llm.url)
    turn = Turn(kind="chat", user="jack")
    got = []

    async def run():
        async for t in eng.run(turn, MSGS):
            got.append(t)
            if len(got) == 3:
                assert eng.cancel(turn.id, "barge-in")

    with pytest.raises(LLMCancelled):
        await run()
    await asyncio.sleep(0.3)
    assert turn.status == "cancelled" and turn.reason == "barge-in"
    assert fake_llm.disconnects == 1 and fake_llm.completed == 0
    assert len(got) < 10
    await eng.llm.close()


async def test_voice_queue_deadline_rejects(fake_llm):
    fake_llm.script.tokens = ["x"] * 30
    fake_llm.script.token_delay = 0.05
    eng = engine_for(fake_llm.url, vq=0.3)
    other_voice = Turn(kind="voice", user="jack")
    late_voice = Turn(kind="voice", user="partner")
    first = asyncio.create_task(collect(eng, other_voice))
    await asyncio.sleep(0.1)
    with pytest.raises(TurnRejected):
        await collect(eng, late_voice)
    assert late_voice.status == "rejected"
    await first
    await eng.llm.close()


async def test_voice_preempts_chat_before_any_text_and_chat_retries(fake_llm):
    fake_llm.script.first_delay = 0.6
    fake_llm.script.tokens = ["ok"]
    eng = engine_for(fake_llm.url, cft=3.0)
    chat = Turn(kind="chat", user="jack")
    chat_task = asyncio.create_task(collect(eng, chat))
    await asyncio.sleep(0.2)  # chat is running, no token yet
    voice = Turn(kind="voice", user="jack")
    fake_llm.script.first_delay = 0.0
    v = await collect(eng, voice)
    c = await chat_task
    assert v == ["ok"] and c == ["ok"]
    assert voice.status == "completed" and chat.status == "completed"
    await eng.llm.close()


async def test_voice_preempts_chat_after_text_ends_chat_early(fake_llm):
    fake_llm.script.tokens = ["x"] * 40
    fake_llm.script.token_delay = 0.05
    eng = engine_for(fake_llm.url)
    chat = Turn(kind="chat", user="jack")
    got = []

    async def run_chat():
        async for t in eng.run(chat, MSGS):
            got.append(t)

    chat_task = asyncio.create_task(run_chat())
    await asyncio.sleep(0.3)
    assert got, "chat should have started streaming"
    fake_llm.script.token_delay = 0.0
    fake_llm.script.tokens = ["spoken"]
    voice = Turn(kind="voice", user="jack")
    assert await collect(eng, voice) == ["spoken"]
    with pytest.raises(TurnPreempted):
        await chat_task
    assert chat.status == "cancelled"
    await eng.llm.close()


async def test_unreachable_server_fails_fast():
    eng = engine_for("http://127.0.0.1:9")  # discard port: connection refused
    t0 = time.monotonic()
    with pytest.raises(LLMUnavailable):
        await collect(eng, Turn(kind="chat", user="jack"))
    assert time.monotonic() - t0 < 2.0
    assert eng.active is None
    await eng.llm.close()


async def test_server_503_is_unavailable(fake_llm):
    fake_llm.script.status = 503
    eng = engine_for(fake_llm.url)
    with pytest.raises(LLMUnavailable):
        await collect(eng, Turn(kind="chat", user="jack"))
    await eng.llm.close()
