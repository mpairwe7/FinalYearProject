"""Cohort-addressable flag rollout.

The defects pinned here are the ones that make a percentage rollout
useless rather than merely wrong: buckets that move between replicas,
buckets that correlate across flags, and a rollout that silently
switches off the population it does not target.

The compatibility suite matters as much as the new behaviour — there
are ~60 ``is_enabled(name)`` call sites that must keep resolving
exactly as they did before.
"""

from __future__ import annotations

import os
import unittest
from unittest import mock

from app.flags import _REGISTRY, FeatureFlags, Flag, Rollout, _bucket_of, is_protected


class BucketingTests(unittest.TestCase):
    def test_bucket_is_stable_across_calls(self) -> None:
        first = _bucket_of("model_tiering", "user-123")
        for _ in range(5):
            self.assertEqual(_bucket_of("model_tiering", "user-123"), first)

    def test_bucket_is_in_range(self) -> None:
        for i in range(500):
            self.assertTrue(0 <= _bucket_of("f", f"user-{i}") < 10_000)

    def test_bucket_does_not_use_salted_builtin_hash(self) -> None:
        """A known digest pins the bucket to SHA-256, not ``hash()``.

        ``hash()`` is salted per interpreter, so a bucket built on it
        would move between replicas and the feature would flicker on
        and off for the same user depending on which pod answered.
        """
        import hashlib

        digest = hashlib.sha256(b"model_tiering:user-123").digest()
        expected = int.from_bytes(digest[:8], "big") % 10_000
        self.assertEqual(_bucket_of("model_tiering", "user-123"), expected)

    def test_buckets_are_independent_across_flags(self) -> None:
        """The same subject must not lead every experiment.

        Hashing the subject alone would put one unlucky cohort into the
        leading edge of every rollout, correlating the results and
        making each experiment unmeasurable.
        """
        subjects = [f"user-{i}" for i in range(400)]
        a = [_bucket_of("flag_a", s) < 1000 for s in subjects]
        b = [_bucket_of("flag_b", s) < 1000 for s in subjects]
        overlap = sum(1 for x, y in zip(a, b, strict=True) if x and y)
        # Independent 10% splits overlap on ~1% of subjects; identical
        # splits would overlap on all ~40 of them.
        self.assertLess(overlap, 15)

    def test_percentage_is_approximately_honoured(self) -> None:
        subjects = [f"tin-{i}" for i in range(4000)]
        selected = sum(1 for s in subjects if _bucket_of("ramp", s) < 25 * 100)
        self.assertAlmostEqual(selected / len(subjects), 0.25, delta=0.03)


class _FlagHarness(unittest.TestCase):
    """Registers a temporary flag so tests do not depend on real ones."""

    flag = Flag("_test_flag", default=False, description="test")

    def setUp(self) -> None:
        _REGISTRY[self.flag.name] = self.flag
        self.addCleanup(_REGISTRY.pop, self.flag.name, None)
        self.flags = FeatureFlags()

    def register(self, **kwargs: object) -> None:
        flag = Flag(name=self.flag.name, **kwargs)  # type: ignore[arg-type]
        _REGISTRY[flag.name] = flag


class BackwardCompatibilityTests(_FlagHarness):
    def test_flag_without_rollout_resolves_from_default(self) -> None:
        self.register(default=True)
        self.assertTrue(self.flags.is_enabled("_test_flag"))
        self.register(default=False)
        self.assertFalse(self.flags.is_enabled("_test_flag"))

    def test_env_var_still_wins_over_default(self) -> None:
        self.register(default=False)
        with mock.patch.dict(os.environ, {"FLAG__TEST_FLAG": "true"}):
            self.assertTrue(self.flags.is_enabled("_test_flag"))

    def test_env_var_means_everyone_not_a_share(self) -> None:
        """An explicit operator switch overrides a percentage rollout."""
        self.register(default=False, rollout=Rollout(percent=1.0))
        with mock.patch.dict(os.environ, {"FLAG__TEST_FLAG": "true"}):
            for i in range(50):
                self.assertTrue(self.flags.is_enabled("_test_flag", subject=f"u{i}"))

    def test_in_memory_override_beats_rollout(self) -> None:
        """The kill switch has to stop a bad release for everyone."""
        self.register(default=False, rollout=Rollout(percent=100.0))
        self.flags.set("_test_flag", False)
        self.assertFalse(self.flags.is_enabled("_test_flag", subject="anyone"))

    def test_unknown_flag_is_false(self) -> None:
        self.assertFalse(self.flags.is_enabled("_no_such_flag"))

    def test_all_still_returns_every_registered_flag(self) -> None:
        self.assertIn("_test_flag", self.flags.all())


class RolloutResolutionTests(_FlagHarness):
    def test_allowlist_beats_the_bucket(self) -> None:
        self.register(default=False, rollout=Rollout(percent=0.0, allowlist=frozenset({"vip"})))
        self.assertTrue(self.flags.is_enabled("_test_flag", subject="vip"))
        self.assertFalse(self.flags.is_enabled("_test_flag", subject="other"))

    def test_cohort_membership_enables(self) -> None:
        self.register(default=False, rollout=Rollout(cohorts=frozenset({"ura_staff"})))
        self.assertTrue(
            self.flags.is_enabled("_test_flag", subject="u1", cohorts={"ura_staff"})
        )
        self.assertFalse(
            self.flags.is_enabled("_test_flag", subject="u1", cohorts={"public"})
        )

    def test_full_percentage_enables_every_subject(self) -> None:
        self.register(default=False, rollout=Rollout(percent=100.0))
        for i in range(50):
            self.assertTrue(self.flags.is_enabled("_test_flag", subject=f"u{i}"))

    def test_untargeted_subject_falls_through_to_default_not_off(self) -> None:
        """Adding a 5% rollout must not disable a default-on flag.

        Collapsing "not targeted" into False would turn a partial
        rollout into a silent outage for the other 95%.
        """
        self.register(default=True, rollout=Rollout(percent=0.001))
        results = [self.flags.is_enabled("_test_flag", subject=f"u{i}") for i in range(50)]
        self.assertTrue(all(results))

    def test_percentage_without_subject_falls_back_to_default(self) -> None:
        self.register(default=False, rollout=Rollout(percent=100.0))
        self.assertFalse(self.flags.is_enabled("_test_flag"))

    def test_percentage_without_subject_warns_once(self) -> None:
        self.register(default=False, rollout=Rollout(percent=50.0))
        with self.assertLogs("app.flags", level="WARNING") as captured:
            self.flags.is_enabled("_test_flag")
            self.flags.is_enabled("_test_flag")
            self.flags.is_enabled("_test_flag")
        self.assertEqual(len(captured.records), 1)

    def test_empty_rollout_is_inert(self) -> None:
        self.register(default=True, rollout=Rollout())
        self.assertTrue(self.flags.is_enabled("_test_flag", subject="u1"))


class EnvRampTests(_FlagHarness):
    def test_percent_env_overrides_the_declared_rollout(self) -> None:
        self.register(default=False, rollout=Rollout(percent=0.0))
        with mock.patch.dict(os.environ, {"FLAG__TEST_FLAG_PERCENT": "100"}):
            self.assertTrue(self.flags.is_enabled("_test_flag", subject="u1"))

    def test_percent_env_accepts_a_trailing_sign(self) -> None:
        self.register(default=False)
        with mock.patch.dict(os.environ, {"FLAG__TEST_FLAG_PERCENT": "100%"}):
            self.assertTrue(self.flags.is_enabled("_test_flag", subject="u1"))

    def test_malformed_percent_does_not_become_everyone(self) -> None:
        self.register(default=False, rollout=Rollout(percent=0.0))
        with (
            mock.patch.dict(os.environ, {"FLAG__TEST_FLAG_PERCENT": "lots"}),
            self.assertLogs("app.flags", level="WARNING"),
        ):
            self.assertFalse(self.flags.is_enabled("_test_flag", subject="u1"))

    def test_percent_env_is_clamped(self) -> None:
        self.register(default=False)
        with mock.patch.dict(os.environ, {"FLAG__TEST_FLAG_PERCENT": "-5"}):
            self.assertFalse(self.flags.is_enabled("_test_flag", subject="u1"))

    def test_cohorts_env_overrides(self) -> None:
        self.register(default=False)
        with mock.patch.dict(os.environ, {"FLAG__TEST_FLAG_COHORTS": "ura_staff, internal"}):
            self.assertTrue(
                self.flags.is_enabled("_test_flag", subject="u1", cohorts={"internal"})
            )

    def test_allowlist_env_overrides(self) -> None:
        self.register(default=False)
        with mock.patch.dict(os.environ, {"FLAG__TEST_FLAG_ALLOWLIST": "tin-9,tin-10"}):
            self.assertTrue(self.flags.is_enabled("_test_flag", subject="tin-9"))
            self.assertFalse(self.flags.is_enabled("_test_flag", subject="tin-11"))

    def test_ramp_is_monotonic(self) -> None:
        """Widening the percentage never drops a subject already inside.

        A user who saw the feature at 5% must still see it at 25%;
        losing it would look like a regression to them.
        """
        self.register(default=False)
        subjects = [f"u{i}" for i in range(300)]

        def enabled_at(pct: str) -> set[str]:
            with mock.patch.dict(os.environ, {"FLAG__TEST_FLAG_PERCENT": pct}):
                return {s for s in subjects if self.flags.is_enabled("_test_flag", subject=s)}

        at5 = enabled_at("5")
        at25 = enabled_at("25")
        at50 = enabled_at("50")
        self.assertTrue(at5 <= at25 <= at50)
        self.assertLess(len(at5), len(at50))


class VariantAndDescribeTests(_FlagHarness):
    def test_variant_labels_each_side(self) -> None:
        self.register(default=False, rollout=Rollout(allowlist=frozenset({"in"})))
        self.assertEqual(self.flags.variant_for("_test_flag", "in"), "on")
        self.assertEqual(self.flags.variant_for("_test_flag", "out"), "off")

    def test_describe_reports_the_effective_rollout(self) -> None:
        self.register(default=False, rollout=Rollout(percent=5.0))
        with mock.patch.dict(os.environ, {"FLAG__TEST_FLAG_PERCENT": "25"}):
            described = self.flags.describe("_test_flag")
        assert described["rollout"] is not None
        self.assertEqual(described["rollout"]["percent"], 25.0)  # type: ignore[index]

    def test_describe_does_not_leak_allowlist_identities(self) -> None:
        """The allowlist holds user ids; the admin view reports its size."""
        self.register(default=False, rollout=Rollout(allowlist=frozenset({"tin-secret"})))
        described = self.flags.describe("_test_flag")
        self.assertNotIn("tin-secret", repr(described))
        self.assertEqual(described["rollout"]["allowlist_size"], 1)  # type: ignore[index]

    def test_describe_is_none_for_a_global_flag(self) -> None:
        self.register(default=True)
        self.assertIsNone(self.flags.describe("_test_flag")["rollout"])


class ProtectedFlagTests(unittest.TestCase):
    def test_safety_flags_cannot_be_toggled_from_the_ui(self) -> None:
        for name in (
            "auth_required",
            "multi_tenant",
            "audit_ledger",
            "ticket_queue",
            "voice_consent",
        ):
            self.assertTrue(is_protected(name), name)

    def test_retrieval_flags_are_not_protected(self) -> None:
        self.assertFalse(is_protected("hyde"))
        self.assertFalse(is_protected("unknown_flag"))


class RegistryIntegrityTests(unittest.TestCase):
    def test_every_phase_30_flag_defaults_off(self) -> None:
        """Nothing from this increment may be on before its gate passes."""
        for name in (
            "multilingual_routing",
            "supervisor_llm_tiebreak",
            "model_tiering",
            "evaluator_optimizer",
            "tax_graph",
            "graph_fusion",
            "mcp_tasks",
            "hyde",
        ):
            self.assertIn(name, _REGISTRY, name)
            self.assertFalse(_REGISTRY[name].default, name)

    def test_no_registered_flag_ships_a_live_rollout(self) -> None:
        """Rollouts are an operational decision, not a code default."""
        for name, flag in _REGISTRY.items():
            if flag.rollout is not None:
                self.assertFalse(flag.rollout.is_addressed(), name)


if __name__ == "__main__":
    unittest.main()
