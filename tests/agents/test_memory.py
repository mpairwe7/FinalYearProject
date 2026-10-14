"""Tests for Phase 16 — personal memory."""

from __future__ import annotations

import time

from app.memory import (
    EpisodicMemory,
    EpisodicSummary,
    FactExtractor,
    SemanticMemory,
    UserFact,
    WorkingMemory,
    compute_decayed_confidence,
    decay_factor,
)
from app.memory.decay import half_life_for
from app.memory.service import _guess_topic_tag, reset_memory_service


# ---------------------------------------------------------------------------
# Decay
# ---------------------------------------------------------------------------
class TestDecay:
    def test_half_life_for_known_category(self):
        assert half_life_for("taxpayer_type") == 5 * 365
        assert half_life_for("current_topic") == 14

    def test_half_life_unknown_category_default(self):
        assert half_life_for("totally_novel") == 90

    def test_decay_factor_zero_age_equals_one(self):
        assert decay_factor(0, 100) == 1.0

    def test_decay_factor_half_life_gives_half(self):
        # Exactly one half-life elapsed → 0.5
        assert decay_factor(100, 100) == 0.5

    def test_decay_factor_two_half_lives(self):
        # Two half-lives → 0.25
        assert decay_factor(200, 100) == 0.25

    def test_compute_decayed_confidence_long_half_life(self):
        # 30 days old, 5-year half-life — barely decays
        now = time.time()
        d = compute_decayed_confidence(
            original_confidence=0.85,
            category="taxpayer_type",
            extracted_at=now - 30 * 86400,
            now=now,
        )
        assert 0.82 < d < 0.86

    def test_compute_decayed_confidence_short_half_life(self):
        now = time.time()
        # 30 days old, 14-day half-life
        d = compute_decayed_confidence(
            original_confidence=0.85,
            category="current_topic",
            extracted_at=now - 30 * 86400,
            now=now,
        )
        assert d < 0.25

    def test_decay_clamped_to_zero_to_one(self):
        # Age so extreme the decay factor would be near zero
        now = time.time()
        d = compute_decayed_confidence(
            original_confidence=0.5,
            category="current_topic",
            extracted_at=now - 1000 * 86400,  # 1000 days
            now=now,
        )
        assert 0.0 <= d < 0.001


# ---------------------------------------------------------------------------
# Working memory
# ---------------------------------------------------------------------------
class TestWorkingMemory:
    def test_set_and_get(self):
        wm = WorkingMemory(ttl_seconds=60)
        wm.set("alice", {"current_topic": "vat"})
        assert wm.get("alice")["current_topic"] == "vat"

    def test_update_merges(self):
        wm = WorkingMemory(ttl_seconds=60)
        wm.update("alice", current_topic="vat")
        wm.update("alice", last_query="how to register")
        v = wm.get("alice")
        assert v["current_topic"] == "vat"
        assert v["last_query"] == "how to register"

    def test_missing_returns_none(self):
        wm = WorkingMemory(ttl_seconds=60)
        assert wm.get("nobody") is None

    def test_clear_removes_key(self):
        wm = WorkingMemory(ttl_seconds=60)
        wm.set("alice", {"x": 1})
        wm.clear("alice")
        assert wm.get("alice") is None

    def test_ttl_expiry(self):
        wm = WorkingMemory(ttl_seconds=0)  # everything expires immediately
        wm.set("alice", {"x": 1})
        time.sleep(0.01)
        assert wm.get("alice") is None

    def test_size_lazy_eviction(self):
        wm = WorkingMemory(ttl_seconds=0)
        wm.set("alice", {"x": 1})
        wm.set("bob", {"y": 2})
        time.sleep(0.01)
        # size() evicts expired keys as a side-effect
        assert wm.size() == 0


# ---------------------------------------------------------------------------
# Episodic memory
# ---------------------------------------------------------------------------
class TestEpisodicMemory:
    def test_write_and_read(self, tmp_db):
        mem = EpisodicMemory()
        summary = EpisodicSummary(
            summary_id="",
            user_id="alice",
            tenant_id="t1",
            conversation_id="c1",
            summary="User asked about VAT",
            topic_tag="vat",
            turn_count=4,
        )
        sid = mem.write(summary)
        assert sid
        rows = mem.list_for_user("alice", tenant_id="t1")
        assert len(rows) == 1
        assert rows[0]["topic_tag"] == "vat"

    def test_topic_filter(self, tmp_db):
        mem = EpisodicMemory()
        mem.write(EpisodicSummary(
            summary_id="", user_id="alice", tenant_id="t1",
            conversation_id="c1", summary="vat q", topic_tag="vat",
        ))
        mem.write(EpisodicSummary(
            summary_id="", user_id="alice", tenant_id="t1",
            conversation_id="c2", summary="paye q", topic_tag="paye",
        ))
        vats = mem.list_for_user("alice", tenant_id="t1", topic_tag="vat")
        assert len(vats) == 1
        assert vats[0]["topic_tag"] == "vat"

    def test_delete_for_user(self, tmp_db):
        mem = EpisodicMemory()
        mem.write(EpisodicSummary(
            summary_id="", user_id="alice", tenant_id="t1",
            conversation_id="c1", summary="x",
        ))
        count = mem.delete_for_user("alice", tenant_id="t1")
        assert count == 1
        assert mem.list_for_user("alice", tenant_id="t1") == []

    def test_user_isolation(self, tmp_db):
        mem = EpisodicMemory()
        mem.write(EpisodicSummary(
            summary_id="", user_id="alice", tenant_id="t1",
            conversation_id="c1", summary="alice's",
        ))
        mem.write(EpisodicSummary(
            summary_id="", user_id="bob", tenant_id="t1",
            conversation_id="c2", summary="bob's",
        ))
        assert len(mem.list_for_user("alice", tenant_id="t1")) == 1
        assert len(mem.list_for_user("bob", tenant_id="t1")) == 1


# ---------------------------------------------------------------------------
# Semantic memory
# ---------------------------------------------------------------------------
class TestSemanticMemory:
    def test_write_and_read(self, tmp_db):
        mem = SemanticMemory()
        fact = UserFact(
            fact_id="",
            user_id="alice",
            tenant_id="t1",
            category="taxpayer_type",
            subject="user",
            predicate="is_a",
            object_value="sole_trader",
            confidence=0.9,
            extracted_at=time.time(),
        )
        fact_id = mem.write(fact)
        assert fact_id
        facts = mem.read("alice", tenant_id="t1")
        assert len(facts) == 1
        assert facts[0].object_value == "sole_trader"

    def test_min_confidence_filter(self, tmp_db):
        mem = SemanticMemory()
        mem.write(UserFact(
            fact_id="", user_id="alice", tenant_id="t1",
            category="industry", subject="user", predicate="in",
            object_value="retail", confidence=0.3,
            extracted_at=time.time(),
        ))
        # Original confidence 0.3 → excluded from min_confidence=0.5
        assert mem.read("alice", tenant_id="t1", min_confidence=0.5) == []

    def test_decay_floor_filter(self, tmp_db):
        mem = SemanticMemory()
        long_ago = time.time() - 60 * 86400  # 60 days
        mem.write(UserFact(
            fact_id="", user_id="alice", tenant_id="t1",
            category="current_topic",  # 14d half-life
            subject="user", predicate="topic",
            object_value="vat", confidence=0.85,
            extracted_at=long_ago,
        ))
        # After 60 days with a 14d half-life, decay ≈ 0.85 * 0.053 ≈ 0.045
        assert mem.read("alice", tenant_id="t1", decay_floor=0.3) == []

    def test_supersede_marks_old_fact(self, tmp_db):
        mem = SemanticMemory()
        f1 = UserFact(
            fact_id="", user_id="alice", tenant_id="t1",
            category="industry", subject="user", predicate="in",
            object_value="retail", confidence=0.8,
            extracted_at=time.time(),
        )
        f1_id = mem.write(f1)
        f2 = UserFact(
            fact_id="", user_id="alice", tenant_id="t1",
            category="industry", subject="user", predicate="in",
            object_value="tech", confidence=0.9,
            extracted_at=time.time(),
        )
        f2_id = mem.write(f2)
        assert mem.supersede(f1_id, f2_id) is True
        # Default read excludes superseded
        facts = mem.read("alice", tenant_id="t1")
        assert all(f.fact_id != f1_id for f in facts)

    def test_changed_taxpayer_type_supersedes_stale_value(self, tmp_db):
        mem = SemanticMemory()
        now = time.time()
        old = UserFact(
            fact_id="", user_id="alice", tenant_id="t1", category="taxpayer_type",
            subject="user", predicate="is_a", object_value="sole_trader",
            confidence=0.85, extracted_at=now,
        )
        old_id = mem.write(old)
        current = UserFact(
            fact_id="", user_id="alice", tenant_id="t1", category="taxpayer_type",
            subject="user", predicate="is_a", object_value="company",
            confidence=0.85, extracted_at=now + 1,
        )
        current_id = mem.write(current)
        facts = mem.read("alice", tenant_id="t1")
        assert [fact.fact_id for fact in facts] == [current_id]
        assert mem.read("alice", tenant_id="t1", include_superseded=True)[-1].fact_id == old_id

    def test_forget_user_cascades(self, tmp_db):
        mem = SemanticMemory()
        for cat in ["a", "b", "c"]:
            mem.write(UserFact(
                fact_id="", user_id="alice", tenant_id="t1",
                category=cat, subject="user", predicate="p",
                object_value="v", confidence=0.8,
                extracted_at=time.time(),
            ))
        count = mem.forget_user("alice", tenant_id="t1")
        assert count == 3
        assert mem.read("alice", tenant_id="t1") == []


# ---------------------------------------------------------------------------
# Fact extractor
# ---------------------------------------------------------------------------
class TestFactExtractor:
    def test_taxpayer_type_sole_trader(self):
        ex = FactExtractor()
        cands = ex.extract([
            {"role": "user", "content": "Hi, I'm a sole trader running a shop."},
        ])
        assert any(c.category == "taxpayer_type" and c.object_value == "sole_trader" for c in cands)

    def test_industry_retail(self):
        ex = FactExtractor()
        cands = ex.extract([
            {"role": "user", "content": "My retail shop needs a TIN"},
        ])
        assert any(c.object_value == "retail" for c in cands)

    def test_vat_registration(self):
        ex = FactExtractor()
        cands = ex.extract([
            {"role": "user", "content": "I am registered for VAT"},
        ])
        assert any(c.object_value == "vat" for c in cands)

    def test_dedupe_keeps_highest_confidence(self):
        ex = FactExtractor()
        # Same fact surfaced twice should appear only once
        cands = ex.extract([
            {"role": "user", "content": "I'm a sole trader."},
            {"role": "user", "content": "Yes, I am a sole trader."},
        ])
        taxpayer_cands = [c for c in cands if c.category == "taxpayer_type"]
        assert len(taxpayer_cands) == 1

    def test_assistant_turns_ignored(self):
        ex = FactExtractor()
        cands = ex.extract([
            {"role": "assistant", "content": "As a sole trader, you would..."},
        ])
        assert not any(c.category == "taxpayer_type" for c in cands)

    def test_empty_input(self):
        assert FactExtractor().extract([]) == []

    def test_local_language_code_switched_facts(self):
        candidates = FactExtractor().extract([
            {"role": "user", "content": "Nze ndi sole trader."},
            {"role": "user", "content": "Nimesajiliwa kwa VAT."},
        ])
        assert any(c.category == "taxpayer_type" and c.object_value == "sole_trader" for c in candidates)
        assert any(c.category == "registered_vat" and c.object_value == "vat" for c in candidates)

    def test_language_mention_is_not_a_language_preference(self):
        candidates = FactExtractor().extract([
            {"role": "user", "content": "Please explain this in Luganda."},
        ])
        assert not any(c.category == "primary_language" for c in candidates)


# ---------------------------------------------------------------------------
# MemoryService — integration
# ---------------------------------------------------------------------------
class TestMemoryService:
    def test_absorb_extracts_and_writes(self, tmp_db):
        reset_memory_service()
        # Need a real user + consent for consent-gated reads
        u = tmp_db.upsert_user(external_id="alice", tenant_id="t1")
        tmp_db.grant_consent(u["id"], "personalization", "2026-04")

        from app.memory import get_memory_service
        mem = get_memory_service()

        outcome = mem.absorb_conversation(
            user_id=u["id"],
            conversation_id="c1",
            tenant_id="t1",
            turns=[
                {"role": "user", "content": "I'm a sole trader in retail"},
            ],
        )
        assert outcome["facts_written"] >= 2
        assert outcome["episodic_written"]

    def test_absorb_extracts_and_writes_from_paired_turns(self, tmp_db):
        reset_memory_service()
        u = tmp_db.upsert_user(external_id="bob", tenant_id="t1")
        tmp_db.grant_consent(u["id"], "personalization", "2026-04")

        from app.memory import get_memory_service
        mem = get_memory_service()

        # Paired turns matching db.get_recent_turns format
        outcome = mem.absorb_conversation(
            user_id=u["id"],
            conversation_id="c2",
            tenant_id="t1",
            turns=[
                {"user_message": "I am a sole trader in transport", "bot_reply": "Noted."},
            ],
        )
        assert outcome["facts_written"] >= 2
        assert outcome["episodic_written"]
        facts = mem.read_facts(u["id"], tenant_id="t1")
        assert any(f.category == "taxpayer_type" and f.object_value == "sole_trader" for f in facts)
        assert any(f.category == "industry" and f.object_value == "transport" for f in facts)

    def test_consent_gate_blocks_reads(self, tmp_db):
        reset_memory_service()
        u = tmp_db.upsert_user(external_id="alice", tenant_id="t1")
        # NO consent granted

        from app.memory import get_memory_service
        mem = get_memory_service()
        outcome = mem.absorb_conversation(
            user_id=u["id"],
            conversation_id="c1",
            tenant_id="t1",
            turns=[{"role": "user", "content": "I'm a sole trader"}],
        )
        assert outcome.get("skipped") == "consent_required"
        assert mem.read_facts(u["id"], tenant_id="t1") == []

    def test_consent_granted_allows_reads(self, tmp_db):
        reset_memory_service()
        u = tmp_db.upsert_user(external_id="alice", tenant_id="t1")
        tmp_db.grant_consent(u["id"], "personalization", "2026-04")

        from app.memory import get_memory_service
        mem = get_memory_service()
        mem.absorb_conversation(
            user_id=u["id"],
            conversation_id="c1",
            tenant_id="t1",
            turns=[{"role": "user", "content": "I'm a sole trader in retail"}],
        )
        facts = mem.read_facts(u["id"], tenant_id="t1")
        assert len(facts) >= 2

    def test_memory_is_tenant_scoped_and_erasure_is_local(self, tmp_db):
        reset_memory_service()
        for tenant in ("t1", "t2"):
            owner = tmp_db.upsert_user(external_id="shared-subject", tenant_id=tenant)
            tmp_db.grant_consent(owner["id"], "personalization", "2026-04")

        from app.memory import get_memory_service
        mem = get_memory_service()
        for tenant in ("t1", "t2"):
            mem.absorb_conversation(
                user_id="shared-subject",
                conversation_id="shared-conversation",
                tenant_id=tenant,
                turns=[{"role": "user", "content": "I'm a sole trader"}],
            )

        assert len(mem.read_facts("shared-subject", tenant_id="t1")) == 1
        assert len(mem.read_facts("shared-subject", tenant_id="t2")) == 1
        mem.forget_user("shared-subject", tenant_id="t1")
        assert mem.read_facts("shared-subject", tenant_id="t1") == []
        assert len(mem.read_facts("shared-subject", tenant_id="t2")) == 1

    def test_working_state_is_shared_tenant_scoped_and_consent_gated(self, tmp_db):
        reset_memory_service()
        for tenant in ("t1", "t2"):
            owner = tmp_db.upsert_user(external_id="shared-subject", tenant_id=tenant)
            tmp_db.grant_consent(owner["id"], "personalization", "2026-04")
        no_consent = tmp_db.upsert_user(external_id="no-consent", tenant_id="t1")

        from app.memory.service import MemoryService
        from app.memory import get_memory_service

        first_process = get_memory_service()
        first_process.update_working("shared-subject", tenant_id="t1", last_topic="vat")
        first_process.update_working(no_consent["id"], tenant_id="t1", last_topic="paye")

        second_process = MemoryService()
        assert second_process.read_all("shared-subject", tenant_id="t1").working == {
            "last_topic": "vat"
        }
        assert second_process.read_all("shared-subject", tenant_id="t2").working is None
        assert second_process.export_user(no_consent["id"], tenant_id="t1")["working"] is None

    def test_repeated_memory_write_is_deduplicated_and_export_is_complete(self, tmp_db):
        reset_memory_service()
        owner = tmp_db.upsert_user(external_id="alice", tenant_id="t1")
        tmp_db.grant_consent(owner["id"], "personalization", "2026-04")

        from app.memory import get_memory_service
        mem = get_memory_service()
        turn = [{"role": "user", "content": "My TIN is 1234567890 and I am a sole trader"}]
        for _ in range(2):
            mem.absorb_conversation(
                user_id=owner["id"],
                conversation_id="stable-conversation",
                tenant_id="t1",
                turns=turn,
            )

        assert len(mem.read_facts(owner["id"], tenant_id="t1")) == 1
        episodes = mem.read_episodic(owner["id"], tenant_id="t1")
        assert len(episodes) == 1
        assert "1234567890" not in episodes[0]["summary"]
        exported = mem.export_user(owner["id"], tenant_id="t1")
        assert len(exported["facts"]) == 1
        assert len(exported["episodic"]) == 1

    def test_absorb_conversation_handles_empty_user_messages_before_content(self, tmp_db):
        reset_memory_service()
        u = tmp_db.upsert_user(external_id="bob", tenant_id="t1")
        tmp_db.grant_consent(u["id"], "personalization", "2026-04")

        from app.memory import get_memory_service
        mem = get_memory_service()
        mem.absorb_conversation(
            user_id=u["id"],
            conversation_id="c_empty_first",
            tenant_id="t1",
            turns=[
                {"role": "user", "content": "   "},
                {"role": "assistant", "content": "Hello!"},
                {"role": "user", "content": "I'm a sole trader in retail"},
                {"role": "assistant", "content": "Got it."},
            ],
        )
        facts = mem.read_facts(u["id"], tenant_id="t1")
        assert len(facts) >= 2
        episodic = mem.read_episodic(u["id"], tenant_id="t1")
        assert len(episodic) >= 1
        assert "sole trader in retail" not in episodic[0]["summary"]

    def test_consent_withdrawal_hides_future_reads(self, tmp_db):
        reset_memory_service()
        u = tmp_db.upsert_user(external_id="alice", tenant_id="t1")
        tmp_db.grant_consent(u["id"], "personalization", "2026-04")

        from app.memory import get_memory_service
        mem = get_memory_service()
        mem.absorb_conversation(
            user_id=u["id"],
            conversation_id="c1",
            tenant_id="t1",
            turns=[{"role": "user", "content": "I'm a sole trader"}],
        )
        assert len(mem.read_facts(u["id"], tenant_id="t1")) > 0

        tmp_db.withdraw_consent(u["id"], "personalization")
        assert mem.read_facts(u["id"], tenant_id="t1") == []

    def test_forget_user_cascade(self, tmp_db):
        reset_memory_service()
        u = tmp_db.upsert_user(external_id="alice", tenant_id="t1")
        tmp_db.grant_consent(u["id"], "personalization", "2026-04")

        from app.memory import get_memory_service
        mem = get_memory_service()
        mem.update_working(u["id"], tenant_id="t1", current_topic="vat")
        mem.absorb_conversation(
            user_id=u["id"],
            conversation_id="c1",
            tenant_id="t1",
            turns=[{"role": "user", "content": "I'm a sole trader in retail"}],
        )
        counts = mem.forget_user(u["id"], tenant_id="t1")
        assert counts["working"] == 1
        assert counts["episodic"] >= 1
        assert counts["semantic"] >= 2

    def test_topic_tag_guess(self):
        assert _guess_topic_tag("how much VAT") == "vat"
        assert _guess_topic_tag("my PAYE") == "paye"
        assert _guess_topic_tag("customs duty on import") == "customs"
        assert _guess_topic_tag("random unrelated query") == "general"
