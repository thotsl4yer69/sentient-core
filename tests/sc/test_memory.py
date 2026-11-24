"""Memory store against the real MiniLM encoder and a real SQLite file."""

from __future__ import annotations

import sqlite3

import pytest

from sc.memory import MemoryStore, people

IDLE = 1800.0

FACTS = [
    ("My dog is called Biscuit and she's a kelpie", "what is my dog's name"),
    ("I drive a 2004 Jaguar X-Type", "what car do I drive"),
    ("My favourite food is lamb rogan josh", "what's my favourite food"),
    ("I'm allergic to penicillin", "what am I allergic to"),
    ("My brother's name is Sam", "what is my brother called"),
    ("I was born in Bendigo", "where was I born"),
    ("I support the Collingwood Magpies", "which footy team do I support"),
    ("My workshop is in the back shed", "where is my workshop"),
    ("I take my coffee black with one sugar", "how do I take my coffee"),
    ("The wifi router is in the hallway cupboard", "where is the wifi router"),
    ("I'm learning to play the bass guitar", "what instrument am I learning"),
    ("My partner's birthday is the 3rd of March", "when is my partner's birthday"),
    ("I hate the smell of coriander", "what smell do I hate"),
    ("My first car was a Holden Commodore", "what was my first car"),
    ("I'm training for a half marathon in May", "what race am I training for"),
    ("The spare house key is under the blue pot", "where is the spare key"),
    ("I work night shifts on Thursdays", "when do I work nights"),
    ("My cat Pixel only eats salmon", "what does my cat eat"),
    ("I want to visit Iceland next winter", "where do I want to travel"),
    ("My Orin Nano is called nanob", "what is my Orin called"),
]


@pytest.fixture
def store(tmp_path, encoder):
    s = MemoryStore(tmp_path / "sc.db", encoder)
    yield s
    s.close()


def test_people_normalises_and_rejects_empty():
    assert people(["Jack", "partner", "jack "]) == ("jack", "partner")
    with pytest.raises(ValueError):
        people(["", " "])


def test_session_reused_within_idle_and_split_after(store):
    a = store.open_session(["jack"], idle_s=IDLE, now=1000.0)
    b = store.open_session(["jack"], idle_s=IDLE, now=1000.0 + 60)
    c = store.open_session(["jack"], idle_s=IDLE, now=1000.0 + 60 + IDLE + 1)
    assert a.is_new and not b.is_new and c.is_new
    assert a.id == b.id != c.id
    d = store.open_session(["partner", "jack"], idle_s=IDLE, now=1100.0)
    assert d.id not in {a.id, c.id} and d.participants == ("jack", "partner")


def test_filler_is_not_remembered(store):
    s = store.open_session(["jack"], idle_s=IDLE)
    for text in ["hi", "thanks!", "ok", "Good morning", "yeah"]:
        assert store.remember(s, "jack", text) is None
    assert store.stats()["memories"] == 0


@pytest.mark.parametrize("text", [
    "What's my dog's name? Answer briefly.",
    "where was I born",
    "Do you remember what my brother is called?",
    "Remind me what the fuse number was",
    "Is the spare key under the pot?",
    "Can you tell me a joke about kelpies",
])
def test_questions_are_not_memories(text):
    assert MemoryStore.worth_remembering(text) == 0.0


def test_explicit_remember_gets_high_importance():
    assert MemoryStore.worth_remembering("Remember that the gate code is 4417") == pytest.approx(0.9)
    assert MemoryStore.worth_remembering("I went to the shops this arvo") == pytest.approx(0.5)


def test_recall_finds_each_fact_across_sessions(store):
    """D1 at the retrieval layer: 20 facts told over 5 sessions; a 6th session
    after reopening the database must retrieve the right one for >= 19/20."""
    t = 10_000.0
    for i, (fact, _) in enumerate(FACTS):
        if i % 4 == 0:
            t += IDLE + 100  # new session every 4 facts -> 5 sessions
        s = store.open_session(["jack"], idle_s=IDLE, now=t)
        assert store.remember(s, "jack", fact, now=t) is not None
    path = store.path
    store.close()
    reopened = MemoryStore(path, store.encoder)
    try:
        s6 = reopened.open_session(["jack"], idle_s=IDLE, now=t + 10 * IDLE)
        assert s6.is_new
        hits = 0
        for fact, question in FACTS:
            got = reopened.recall(s6, question, k=6, min_score=0.35, now=t + 10 * IDLE)
            hits += any(m.text == fact for m in got)
        assert hits >= 19, f"recalled {hits}/20"
        assert reopened.stats()["sessions"] == 6
    finally:
        reopened.close()


def test_privacy_rule_never_shows_memory_to_someone_absent(store):
    """D2 at the retrieval layer: P ⊆ participants."""
    jack_alone = store.open_session(["jack"], idle_s=IDLE)
    both = store.open_session(["jack", "partner"], idle_s=IDLE)
    partner_alone = store.open_session(["partner"], idle_s=IDLE)
    with_guest = store.open_session(["jack", "guest"], idle_s=IDLE)
    private = [
        "My PIN for the safe is in the red notebook",
        "I'm planning a surprise trip to Tasmania for my partner",
        "I'm worried about the job interview on Friday",
        "My bank is ING and I keep savings there",
        "I've been having trouble sleeping lately",
    ]
    for text in private:
        store.remember(jack_alone, "jack", text)
    shared = "We're getting the bathroom retiled in December"
    store.remember(both, "partner", shared)

    probes = ["where is the safe PIN", "surprise trip", "job interview", "which bank", "sleep",
              "bathroom renovation"]
    seen_by_partner = {m.text for q in probes for m in store.recall(partner_alone, q, k=6, min_score=0.0)}
    seen_together = {m.text for q in probes for m in store.recall(both, q, k=6, min_score=0.0)}
    seen_with_guest = {m.text for q in probes for m in store.recall(with_guest, q, k=6, min_score=0.0)}
    seen_by_jack = {m.text for q in probes for m in store.recall(jack_alone, q, k=6, min_score=0.0)}

    assert not (seen_by_partner & set(private)), "partner saw Jack's private memories"
    assert not (seen_together & set(private)), "a joint session saw Jack's private memories"
    assert not seen_with_guest, "a guest session saw memories the guest wasn't present for"
    assert shared in seen_by_partner and shared in seen_together and shared in seen_by_jack
    assert set(private) <= seen_by_jack


def test_near_duplicate_refreshes_instead_of_inserting(store):
    s = store.open_session(["jack"], idle_s=IDLE)
    a = store.remember(s, "jack", "My dog is called Biscuit", now=100.0)
    b = store.remember(s, "jack", "My dog is called Biscuit.", now=200.0)
    assert a == b and store.stats()["memories"] == 1
    other = store.open_session(["partner"], idle_s=IDLE)
    c = store.remember(other, "partner", "My dog is called Biscuit", now=300.0)
    assert c != a, "same words from a different audience must stay separate"


def test_forget_removes_from_recall(store):
    s = store.open_session(["jack"], idle_s=IDLE)
    mid = store.remember(s, "jack", "The shed padlock combination is written on the fridge")
    assert store.recall(s, "shed padlock", k=3, min_score=0.3)
    assert store.forget([mid]) == 1
    assert not store.recall(s, "shed padlock", k=3, min_score=0.3)


def test_history_and_log(store):
    s = store.open_session(["jack"], idle_s=IDLE)
    for i in range(12):
        store.log(s, "user", "jack", f"q{i}")
        store.log(s, "assistant", "cortana", f"a{i}")
    h = store.history(s, 3)
    assert [m.content for m in h] == ["q9", "a9", "q10", "a10", "q11", "a11"]


def test_backup_is_consistent(store, tmp_path):
    s = store.open_session(["jack"], idle_s=IDLE)
    store.remember(s, "jack", "Backups should include this memory about the garage door")
    dest = store.backup(tmp_path / "bk" / "sc.db")
    con = sqlite3.connect(dest)
    try:
        assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert con.execute("SELECT COUNT(*) FROM memories").fetchone()[0] == 1
    finally:
        con.close()


def test_newer_schema_is_refused(tmp_path, encoder):
    path = tmp_path / "sc.db"
    MemoryStore(path, encoder).close()
    con = sqlite3.connect(path)
    con.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
    con.commit()
    con.close()
    with pytest.raises(RuntimeError, match="newer"):
        MemoryStore(path, encoder)
