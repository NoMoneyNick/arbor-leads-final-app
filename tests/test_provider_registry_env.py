"""
test_provider_registry_env.py -- 2026-09-18 review, Section 1: "Confirm
there are exactly three configurable, provider-independent slots: primary,
backup 1 and backup 2. All companies remain unchosen. Test with fake
providers, and keep unconfigured slots disabled."

Covers letter_providers.registry.build_registry_from_env(), the factory
added this review that turns LETTER_PROVIDER_PRIMARY / _BACKUP_1 / _BACKUP_2
into a ProviderRegistry with exactly three ProviderSlot entries, in that
fixed priority order, and the fix to FakeLetterProvider (its own docstring
already claimed FAKE_PROVIDER_FORCE_OUTCOME worked, but `import os` was
unused -- the env var had no effect until this review).

Run with:
    python -m unittest tests.test_provider_registry_env -v
"""
import os
import sys
import unittest
from contextlib import contextmanager

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from letter_providers.registry import (
    build_registry_from_env, SLOT_ROLES, LETTER_PROVIDER_ENV_VARS, _UnconfiguredSlotProvider,
)
from letter_providers.fake_provider import FakeLetterProvider
from letter_providers.stannp_provider import StannpProvider
from letter_providers.base import LetterRequest


@contextmanager
def _env(**kwargs):
    """Sets (or, with None, unsets) the given env vars for the duration of
    the block and restores exactly what was there before -- so tests never
    leak LETTER_PROVIDER_*/FAKE_PROVIDER_* state into other test files run
    in the same `unittest discover` process (the same discipline
    test_reconciliation.py's `_pipeline` context manager established for
    LETTER_DISPATCH_PIPELINE)."""
    previous = {k: os.environ.get(k) for k in kwargs}
    try:
        for k, v in kwargs.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        yield
    finally:
        for k, v in previous.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


_ALL_PROVIDER_ENV_VARS = dict.fromkeys(LETTER_PROVIDER_ENV_VARS.values(), None)


class TestExactlyThreeSlots(unittest.TestCase):

    def test_all_unset_still_produces_exactly_three_slots(self):
        with _env(**_ALL_PROVIDER_ENV_VARS):
            registry = build_registry_from_env()
        self.assertEqual(len(registry.slots), 3)
        self.assertEqual([s.role for s in registry.slots], list(SLOT_ROLES))

    def test_slot_roles_are_primary_backup_1_backup_2_in_that_order(self):
        self.assertEqual(SLOT_ROLES, ("primary", "backup_1", "backup_2"))
        with _env(LETTER_PROVIDER_PRIMARY="fake_test", LETTER_PROVIDER_BACKUP_1="fake_test",
                  LETTER_PROVIDER_BACKUP_2="fake_test"):
            registry = build_registry_from_env()
        self.assertEqual([s.role for s in registry.slots], ["primary", "backup_1", "backup_2"])

    def test_partial_configuration_still_produces_three_slots_not_one(self):
        """Only the primary configured -- the other two must still EXIST
        (as disabled slots), not be silently dropped from the list."""
        with _env(LETTER_PROVIDER_PRIMARY="fake_test", **{k: None for k in
                   (LETTER_PROVIDER_ENV_VARS["backup_1"], LETTER_PROVIDER_ENV_VARS["backup_2"])}):
            registry = build_registry_from_env()
        self.assertEqual(len(registry.slots), 3)
        self.assertTrue(registry.slots[0].enabled)
        self.assertFalse(registry.slots[1].enabled)
        self.assertFalse(registry.slots[2].enabled)


class TestAllCompaniesRemainUnchosenByDefault(unittest.TestCase):
    """'All companies remain unchosen' -- with no LETTER_PROVIDER_* env
    vars set at all (the real out-of-the-box state), no slot may be usable,
    regardless of estimated cost."""

    def test_default_registry_has_no_usable_slots(self):
        with _env(**_ALL_PROVIDER_ENV_VARS):
            registry = build_registry_from_env()
        self.assertEqual(registry.usable_slots(), [])
        self.assertEqual(registry.usable_slots(estimated_cost_pence=1), [])

    def test_default_slots_are_all_disabled_and_unconfigured(self):
        with _env(**_ALL_PROVIDER_ENV_VARS):
            registry = build_registry_from_env()
        for slot in registry.slots:
            self.assertFalse(slot.enabled)
            self.assertFalse(slot.adapter.is_configured())
            self.assertIsInstance(slot.adapter, _UnconfiguredSlotProvider)

    def test_unconfigured_slot_provider_refuses_to_send_if_ever_reached(self):
        provider = _UnconfiguredSlotProvider("primary")
        request = LetterRequest(idempotency_key="k", lead_reference="PLANIT-001",
                                 address_lines={"line1": "1 Real St", "postcode": "LS1 1AA"},
                                 applicant_name="A", content_html="<p>x</p>",
                                 content_fingerprint="fp")
        with self.assertRaises(RuntimeError):
            provider.send(request)


class TestUnrecognisedProviderKindFailsSafeToDisabled(unittest.TestCase):

    def test_typo_or_unknown_kind_disables_the_slot_rather_than_guessing(self):
        with _env(LETTER_PROVIDER_PRIMARY="stanp_typo",  # missing an 'n'
                  **{k: None for k in (LETTER_PROVIDER_ENV_VARS["backup_1"], LETTER_PROVIDER_ENV_VARS["backup_2"])}):
            registry = build_registry_from_env()
        self.assertFalse(registry.slots[0].enabled)
        self.assertFalse(registry.slots[0].adapter.is_configured())
        self.assertEqual(registry.usable_slots(), [])


class TestProviderIndependenceOfSlots(unittest.TestCase):
    """The role (primary/backup_1/backup_2) and the provider kind occupying
    it are fully independent -- nothing hard-codes e.g. 'primary is
    always Stannp'. Proven by putting the SAME provider kind in different
    slots and different kinds in the same role across two builds."""

    def test_same_kind_can_occupy_any_slot(self):
        with _env(LETTER_PROVIDER_PRIMARY="fake_test",
                  **{k: None for k in (LETTER_PROVIDER_ENV_VARS["backup_1"], LETTER_PROVIDER_ENV_VARS["backup_2"])}):
            reg_a = build_registry_from_env()
        with _env(LETTER_PROVIDER_BACKUP_2="fake_test",
                  **{k: None for k in (LETTER_PROVIDER_ENV_VARS["primary"], LETTER_PROVIDER_ENV_VARS["backup_1"])}):
            reg_b = build_registry_from_env()
        self.assertIsInstance(reg_a.slots[0].adapter, FakeLetterProvider)
        self.assertIsInstance(reg_b.slots[2].adapter, FakeLetterProvider)
        self.assertFalse(reg_b.slots[0].enabled)

    def test_stannp_and_fake_test_can_coexist_in_different_slots(self):
        with _env(LETTER_PROVIDER_PRIMARY="stannp", LETTER_PROVIDER_BACKUP_1="fake_test",
                  **{LETTER_PROVIDER_ENV_VARS["backup_2"]: None}):
            registry = build_registry_from_env()
        self.assertIsInstance(registry.slots[0].adapter, StannpProvider)
        self.assertIsInstance(registry.slots[1].adapter, FakeLetterProvider)
        # Stannp has no credentials in this sandbox -- correctly unusable
        # even though the slot itself is "enabled" (configured != usable).
        self.assertTrue(registry.slots[0].enabled)
        self.assertFalse(registry.slots[0].adapter.is_configured())
        self.assertTrue(registry.slots[1].adapter.is_configured())


class TestBuiltFromEnvReadsFreshEveryCall(unittest.TestCase):
    """Mirrors fulfilment.active_pipeline()'s own contract (no caching) --
    an operator changing LETTER_PROVIDER_* and restarting/re-reading config
    takes effect without needing a code change."""

    def test_two_calls_reflect_two_different_env_states(self):
        with _env(LETTER_PROVIDER_PRIMARY="fake_test",
                  **{k: None for k in (LETTER_PROVIDER_ENV_VARS["backup_1"], LETTER_PROVIDER_ENV_VARS["backup_2"])}):
            reg1 = build_registry_from_env()
        with _env(**_ALL_PROVIDER_ENV_VARS):
            reg2 = build_registry_from_env()
        self.assertTrue(reg1.slots[0].enabled)
        self.assertFalse(reg2.slots[0].enabled)


class TestFakeProviderReadsForceOutcomeFromEnv(unittest.TestCase):
    """The bug this review found: FakeLetterProvider's own docstring
    claimed FAKE_PROVIDER_FORCE_OUTCOME worked, but `import os` was unused
    -- the env var had no effect. Fixed as part of wiring the env-driven
    factory (Section 1), since 'test with fake providers' via pure
    configuration depends on it."""

    def test_env_var_forces_rejected_outcome_with_no_constructor_argument(self):
        with _env(FAKE_PROVIDER_FORCE_OUTCOME="rejected"):
            provider = FakeLetterProvider()
        request = LetterRequest(idempotency_key="k1", lead_reference="PLANIT-001",
                                 address_lines={"line1": "1 Real St", "postcode": "LS1 1AA"},
                                 applicant_name="A", content_html="<p>x</p>", content_fingerprint="fp")
        result = provider.send(request)
        self.assertEqual(result.outcome, "rejected")

    def test_explicit_constructor_argument_overrides_env(self):
        with _env(FAKE_PROVIDER_FORCE_OUTCOME="rejected"):
            provider = FakeLetterProvider(force_outcome="accepted")
        request = LetterRequest(idempotency_key="k2", lead_reference="PLANIT-002",
                                 address_lines={"line1": "1 Real St", "postcode": "LS1 1AA"},
                                 applicant_name="A", content_html="<p>x</p>", content_fingerprint="fp")
        result = provider.send(request)
        self.assertEqual(result.outcome, "accepted")

    def test_env_var_reject_postcodes_is_comma_split_and_upper_cased(self):
        with _env(FAKE_PROVIDER_REJECT_POSTCODES="ls1 1aa, LS2 2BB"):
            provider = FakeLetterProvider()
        self.assertEqual(provider.reject_postcodes, {"LS1 1AA", "LS2 2BB"})


class TestRegistryDescribeIsAdminVisible(unittest.TestCase):
    """Section 1 + general admin-visibility expectation from the review:
    an operator/admin surface must be able to see all three slots' state,
    including that a slot is deliberately unconfigured -- not infer it from
    absence."""

    def test_describe_lists_all_three_roles_even_when_unconfigured(self):
        with _env(**_ALL_PROVIDER_ENV_VARS):
            registry = build_registry_from_env()
        described = registry.describe()
        self.assertEqual([d["role"] for d in described], ["primary", "backup_1", "backup_2"])
        self.assertTrue(all(d["enabled"] is False for d in described))
        self.assertTrue(all(d["configured"] is False for d in described))


if __name__ == "__main__":
    unittest.main()
