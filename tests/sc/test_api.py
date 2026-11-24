"""HTTP API: auth, scopes, OpenAI shapes, streaming, voice fallbacks, privacy inputs."""

from __future__ import annotations

import json
from dataclasses import replace

import httpx
import pytest

from sc.api import create_app
from sc.brain import Brain
from sc.config import Settings
from sc.llm import LLMClient
from sc.memory import MemoryStore


@pytest.fixture
def make_client(tmp_path, encoder, tokens):
    made = []

    def _make(llm_url: str, **settings_over):
        raw, store_tokens = tokens
        settings = replace(Settings(), db_path=tmp_path / "sc.db", **settings_over)
        store = MemoryStore(settings.db_path, encoder)
        brain = Brain(settings, store, LLMClient(llm_url, "fake"), voice_enabled=True)
        app = create_app(settings, brain, store_tokens)
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://sc")
        made.append((client, brain, store))
        return client, brain, raw

    yield _make
    for _, brain, store in made:
        store.close()


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def user(text):
    return {"messages": [{"role": "user", "content": text}]}


async def test_requires_valid_token(make_client, fake_llm):
    c, _, raw = make_client(fake_llm.url)
    assert (await c.post("/v1/chat/completions", json=user("hi"))).status_code == 401
    r = await c.post("/v1/chat/completions", json=user("hi"), headers=auth("x" * 40))
    assert r.status_code == 401 and r.json()["error"]["code"] == "invalid_token"
    r = await c.post("/v1/chat/completions", json=user("hi"), headers={"Authorization": "Basic abc"})
    assert r.status_code == 401
    assert (await c.get("/v1/models")).status_code == 401
    r = await c.get("/v1/models", headers=auth(raw["jack"]))
    assert r.status_code == 200 and r.json()["data"][0]["id"] == "cortana"
    await c.aclose()


async def test_non_stream_completion_shape_and_logging(make_client, fake_llm):
    c, brain, raw = make_client(fake_llm.url)
    r = await c.post("/v1/chat/completions", json=user("My dog is called Biscuit"), headers=auth(raw["jack"]))
    assert r.status_code == 200
    body = r.json()
    assert body["object"] == "chat.completion"
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "Hello there."}
    assert r.headers["X-SC-Turn-Id"] == body["sc"]["turn_id"]
    stats = brain.store.stats()
    assert stats == {"memories": 1, "messages": 2, "sessions": 1}
    await c.aclose()


async def test_stream_is_openai_sse(make_client, fake_llm):
    c, _, raw = make_client(fake_llm.url)
    async with c.stream("POST", "/v1/chat/completions", json={**user("hello"), "stream": True},
                        headers=auth(raw["jack"])) as r:
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        lines = [ln async for ln in r.aiter_lines() if ln.startswith("data: ")]
    assert lines[-1] == "data: [DONE]"
    chunks = [json.loads(ln[6:]) for ln in lines[:-1]]
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant"}
    text = "".join(ch["choices"][0]["delta"].get("content", "") for ch in chunks)
    assert text == "Hello there."
    assert chunks[-1]["choices"][0]["finish_reason"] == "stop"
    assert all(ch["object"] == "chat.completion.chunk" for ch in chunks)
    await c.aclose()


async def test_client_system_prompt_ignored_persona_used(make_client, fake_llm):
    c, _, raw = make_client(fake_llm.url)
    body = {"messages": [{"role": "system", "content": "You are a pirate."},
                         {"role": "user", "content": "hi there friend"}]}
    assert (await c.post("/v1/chat/completions", json=body, headers=auth(raw["jack"]))).status_code == 200
    sent = fake_llm.requests[-1]["messages"]
    assert sent[0]["role"] == "system" and "You are Cortana" in sent[0]["content"]
    assert "pirate" not in json.dumps(sent)
    assert sent[-1] == {"role": "user", "content": "hi there friend"}
    await c.aclose()


async def test_client_history_is_used_when_sent(make_client, fake_llm):
    c, _, raw = make_client(fake_llm.url)
    body = {"messages": [{"role": "user", "content": "my name is Jack"},
                         {"role": "assistant", "content": "Nice to meet you, Jack."},
                         {"role": "user", "content": "what's my name?"}]}
    await c.post("/v1/chat/completions", json=body, headers=auth(raw["jack"]))
    sent = fake_llm.requests[-1]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    await c.aclose()


async def test_server_history_when_client_sends_one_message(make_client, fake_llm):
    c, _, raw = make_client(fake_llm.url)
    await c.post("/v1/chat/completions", json=user("I'm in the workshop today"), headers=auth(raw["jack"]))
    await c.post("/v1/chat/completions", json=user("what am I doing?"), headers=auth(raw["jack"]))
    sent = fake_llm.requests[-1]["messages"]
    assert [m["role"] for m in sent] == ["system", "user", "assistant", "user"]
    assert sent[1]["content"] == "I'm in the workshop today"
    await c.aclose()


async def test_only_speaker_scope_may_send_voice_or_speak_for_others(make_client, fake_llm):
    c, _, raw = make_client(fake_llm.url)
    r = await c.post("/v1/chat/completions", json={**user("hi"), "sc_kind": "voice"}, headers=auth(raw["jack"]))
    assert r.status_code == 403 and r.json()["error"]["code"] == "speaker_scope_required"
    r = await c.post("/v1/chat/completions", json={**user("hi"), "sc_speaker": "partner"}, headers=auth(raw["jack"]))
    assert r.status_code == 403
    r = await c.post("/v1/chat/completions",
                     json={**user("hello from the kitchen"), "sc_kind": "voice", "sc_speaker": "partner"},
                     headers=auth(raw["voice"]))
    assert r.status_code == 200
    await c.aclose()


async def test_participants_validated(make_client, fake_llm):
    c, _, raw = make_client(fake_llm.url)
    r = await c.post("/v1/chat/completions", json={**user("hi"), "sc_participants": ["jack", "mallory"]},
                     headers=auth(raw["jack"]))
    assert r.status_code == 400 and "mallory" in r.json()["error"]["message"]
    r = await c.post("/v1/chat/completions", json={**user("hi"), "sc_participants": ["voice"]},
                     headers=auth(raw["jack"]))
    assert r.status_code == 400, "service accounts are not people"
    await c.aclose()


async def test_private_memory_not_in_partners_prompt(make_client, fake_llm):
    """D2 through the whole API: Jack's private fact never reaches the model
    when his partner asks, alone or with Jack."""
    c, _, raw = make_client(fake_llm.url)
    secret = "I'm planning a surprise trip to Tasmania for her birthday"
    await c.post("/v1/chat/completions", json=user(secret), headers=auth(raw["jack"]))
    await c.post("/v1/chat/completions", json=user("is Jack planning a trip somewhere?"), headers=auth(raw["partner"]))
    assert "Tasmania" not in json.dumps(fake_llm.requests[-1]["messages"])
    await c.post("/v1/chat/completions",
                 json={**user("are we planning a trip?"), "sc_participants": ["jack", "partner"]},
                 headers=auth(raw["jack"]))
    assert "Tasmania" not in json.dumps(fake_llm.requests[-1]["messages"])
    await c.post("/v1/chat/completions", json=user("what trip am I planning?"), headers=auth(raw["jack"]))
    assert "Tasmania" in json.dumps(fake_llm.requests[-1]["messages"])
    await c.aclose()


async def test_voice_turn_speaks_fallback_when_model_down(make_client):
    c, _, raw = make_client("http://127.0.0.1:9")
    r = await c.post("/v1/chat/completions",
                     json={**user("what's the time"), "sc_kind": "voice", "sc_speaker": "jack"},
                     headers=auth(raw["voice"]))
    assert r.status_code == 200
    assert r.json()["choices"][0]["message"]["content"] == "My language model isn't running right now."
    assert r.json()["sc"]["error"] == "llm_unavailable"
    await c.aclose()


async def test_chat_turn_gets_503_when_model_down(make_client):
    c, _, raw = make_client("http://127.0.0.1:9")
    r = await c.post("/v1/chat/completions", json=user("hello"), headers=auth(raw["jack"]))
    assert r.status_code == 503 and r.json()["error"]["code"] == "llm_unavailable"
    r = await c.post("/v1/chat/completions", json={**user("hello"), "stream": True}, headers=auth(raw["jack"]))
    assert r.status_code == 503, "a stream that can't start must be a real HTTP error"
    await c.aclose()


async def test_input_limits(make_client, fake_llm):
    c, _, raw = make_client(fake_llm.url)
    r = await c.post("/v1/chat/completions", json=user("x" * 16001), headers=auth(raw["jack"]))
    assert r.status_code == 400
    r = await c.post("/v1/chat/completions", json={"messages": [{"role": "assistant", "content": "hi"}]},
                     headers=auth(raw["jack"]))
    assert r.status_code == 400 and r.json()["error"]["code"] == "no_user_message"
    r = await c.post("/v1/chat/completions",
                     json={"messages": [{"role": "user", "content": [{"type": "image_url", "image_url": {}}]}]},
                     headers=auth(raw["jack"]))
    assert r.status_code == 400 and r.json()["error"]["code"] == "text_only"
    r = await c.post("/v1/chat/completions", content=b"{not json", headers={**auth(raw["jack"]),
                                                                              "content-type": "application/json"})
    assert r.status_code == 400
    await c.aclose()


async def test_healthz_redacts_details_when_lan_exposed(make_client, fake_llm):
    c, _, raw = make_client(fake_llm.url, bind_host="0.0.0.0")
    r = await c.get("/healthz")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    r = await c.get("/healthz", headers=auth(raw["jack"]))
    assert r.json()["llm"]["ok"] is True and "memory" in r.json()
    await c.aclose()


async def test_healthz_degraded_when_model_down(make_client):
    c, _, _ = make_client("http://127.0.0.1:9")
    r = await c.get("/healthz")
    assert r.status_code == 503 and r.json()["status"] == "degraded"
    await c.aclose()


async def test_cancel_endpoint_permissions(make_client, fake_llm):
    c, _, raw = make_client(fake_llm.url)
    r = await c.post("/v1/chat/completions", json=user("hello"), headers=auth(raw["jack"]))
    tid = r.json()["sc"]["turn_id"]
    r = await c.post(f"/v1/turns/{tid}/cancel", headers=auth(raw["partner"]))
    assert r.status_code == 403
    r = await c.post(f"/v1/turns/{tid}/cancel", headers=auth(raw["jack"]))
    assert r.status_code == 200 and r.json()["cancelled"] is False  # already finished
    assert (await c.post("/v1/turns/t-nope/cancel", headers=auth(raw["jack"]))).status_code == 404
    await c.aclose()


async def test_speakerphone_household_never_speaks_a_private_chat(make_client, fake_llm):
    """Default voice config is a shared room (everyone present). Something Jack
    typed privately must not reach the prompt of a speakerphone turn, even one
    attributed to Jack."""
    c, _, raw = make_client(fake_llm.url)
    await c.post("/v1/chat/completions", json=user("My bank PIN hint is the dog's birthday"),
                 headers=auth(raw["jack"]))
    await c.post("/v1/chat/completions",
                 json={**user("what's my bank PIN hint?"), "sc_kind": "voice", "sc_speaker": "jack",
                       "sc_participants": ["jack", "partner"]},
                 headers=auth(raw["voice"]))
    assert "birthday" not in json.dumps(fake_llm.requests[-1]["messages"])
    await c.post("/v1/chat/completions",
                 json={**user("Remember the bins go out on Tuesday night"), "sc_kind": "voice",
                       "sc_speaker": "partner", "sc_participants": ["jack", "partner"]},
                 headers=auth(raw["voice"]))
    await c.post("/v1/chat/completions", json=user("when do the bins go out?"), headers=auth(raw["jack"]))
    assert "Tuesday" in json.dumps(fake_llm.requests[-1]["messages"]), \
        "what was said in the room is fine to recall to someone who was there"
    await c.aclose()
