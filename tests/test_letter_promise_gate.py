"""
test_letter_promise_gate.py -- 2026-09-18 review, Section 4 (first pass)
and Section 3, second pass: "Confirm that the public posting promise is
controlled by executable configuration/feature gates -- not merely
comments or launch-checklist wording. Test both disabled and enabled
customer journeys." / "Couple the public posting promise to the correct
fulfilment pipeline and a configured, enabled real provider. Keep explicit
launch approval as well. Test invalid configuration combinations so a
flag alone cannot advertise an unavailable service."

Drives the REAL payments.py (PLANS, plan_description, plan_roi) and the REAL
fulfilment.letter_sending_live(), not mocks of either -- this is the
executable gate itself under test, not a description of it.

Proves, with evidence:
  1. Default (everything unset): NO plan's customer-facing description or
     ROI text mentions a posted letter, for every plan key in PLANS,
     including every single-lead tier and every subscription tier that
     used to hardcode the letter sentence directly.
  2. Explicit LETTER_SENDING_LIVE=false / "0": same as unset -- the flag is
     genuinely a truthy-value check, not "set at all".
  3. LETTER_SENDING_LIVE=true (and other truthy spellings) -- PLUS the
     pipeline set to 'fulfilment' AND a real, enabled provider configured
     (see Section 3 second-pass coverage below) -- every plan that HAS a
     letter_suffix/roi_letter_suffix now includes the letter sentence in
     both plan_description() and plan_roi(); plans with no letter suffix
     (growth, arb_consultant, commercial_forestry, treekey_elite) are
     byte-for-byte unaffected either way.
  4. Fresh-read-per-call: no caching -- flipping the env var between two
     calls in the same process changes the result immediately, matching
     the fulfilment.active_pipeline() precedent this codebase already
     established for config reads.
  5. _resolve_live_single_lead_price's synthesized "live" dict (used by the
     live-price checkout path) goes through the same gate, not a raw
     PLANS[...]["description"] read -- regression guard for the exact bug
     class this section closes.
  6. create_checkout_session's Stripe product_description (both the
     static-plan path and the live-price path) reflects the gate.
  7. (Section 3, second pass) Every INVALID two-out-of-three combination
     -- flag on with the legacy pipeline, flag on with no provider
     configured, flag on with only the fake/test provider configured,
     flag on with a real provider that's configured but disabled, pipeline
     +provider ready but the flag itself left off -- leaves the promise
     OFF. Proves the flag alone can never advertise a service that isn't
     actually able to run, in either direction (missing pipeline, missing
     provider, or missing explicit approval).

Run with:
    python -m unittest tests.test_letter_promise_gate -v
"""
import os
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _APP_DIR)

# --- stripe isn't installed in this sandbox -- same stub technique as
# test_payments_webhook.py (see that file's docstring for why this must be
# unconditional, not "if absent", under `unittest discover`). -------------
_fake_stripe = types.ModuleType("stripe")
_fake_stripe.api_key = ""


class _FakeSignatureVerificationError(Exception):
    pass


class _FakeErrorNS:
    SignatureVerificationError = _FakeSignatureVerificationError


_fake_stripe.error = _FakeErrorNS
_fake_stripe.Webhook = MagicMock()
_fake_stripe.Refund = MagicMock()
_fake_stripe.checkout = MagicMock()
sys.modules["stripe"] = _fake_stripe
sys.modules["stripe.error"] = types.ModuleType("stripe.error")
sys.modules["stripe.error"].SignatureVerificationError = _FakeSignatureVerificationError

# See test_payments_webhook.py's copy of this comment for the full
# explanation of why this swap is safe: address_release.py binds
# "database" at its OWN module-import time (same early import chain as
# main.py, well before this file runs), so it's unaffected by this file
# replacing sys.modules["database"] with a fresh object here.
for _name in ("database", "notifications", "payments"):
    if _name in sys.modules:
        del sys.modules[_name]

for _name in ("database", "notifications"):
    sys.modules[_name] = types.ModuleType(_name)

import fulfilment  # REAL module -- letter_sending_live() must be genuine
import payments  # noqa: E402 -- REAL module, freshly (re)imported above

# payments.py reads stripe.api_key once at import time from
# STRIPE_SECRET_KEY (empty in this sandbox); create_checkout_session bails
# out immediately (a config error, not the behaviour under test here) when
# it's falsy. Set it directly on the already-imported stripe stub, same
# fix as test_payments_webhook.py uses for STRIPE_WEBHOOK_SECRET.
payments.stripe.api_key = "sk_test_dummy"

_ENV_KEY = fulfilment.LETTER_SENDING_LIVE_ENV
_LETTER_SENTENCE_FRAGMENTS = ("introduction letter", "posted introduction letter", "printed & posted")

# 2026-09-18 review, Section 3 (second pass): letter_sending_live() now also
# reads the pipeline flag and the provider registry's env vars -- every test
# in this file must isolate ALL of these, not just LETTER_SENDING_LIVE, or a
# provider/pipeline setting from one test (or an earlier test file) could
# leak into another's assertions.
_ALL_GATE_ENV_KEYS = (
    _ENV_KEY,
    fulfilment.LETTER_DISPATCH_PIPELINE_ENV,
    "LETTER_PROVIDER_PRIMARY", "LETTER_PROVIDER_BACKUP_1", "LETTER_PROVIDER_BACKUP_2",
    "STANNP_API_KEY", "STANNP_TEMPLATE_ID",
)


def _clear_flag():
    os.environ.pop(_ENV_KEY, None)


def _set_flag(value):
    os.environ[_ENV_KEY] = value


def _set_pipeline_fulfilment():
    os.environ[fulfilment.LETTER_DISPATCH_PIPELINE_ENV] = "fulfilment"


def _set_pipeline_legacy():
    """Explicit legacy, not just "unset" -- exercises the same code path
    (active_pipeline() falling back to 'legacy') a fresh unconfigured
    deploy would actually see."""
    os.environ[fulfilment.LETTER_DISPATCH_PIPELINE_ENV] = "legacy"


def _configure_real_provider():
    """Stannp is the only provider adapter in letter_providers/ whose
    is_configured() can ever report True (intelliprint/postworks are
    unimplemented placeholders hard-coded to always report unconfigured --
    see their own module docstrings) -- so this is the only way to
    genuinely exercise the "a real provider is configured and enabled"
    branch of letter_sending_live() without inventing a vendor
    integration. Real, non-fake credentials aren't needed for this gate
    (is_configured() only checks presence, never calls out) -- dummy
    values are enough."""
    os.environ["LETTER_PROVIDER_PRIMARY"] = "stannp"
    os.environ["STANNP_API_KEY"] = "sk_test_dummy_stannp_key"
    os.environ["STANNP_TEMPLATE_ID"] = "tmpl_dummy"


def _configure_only_fake_provider():
    """A provider IS configured and enabled -- just not a real one. This
    must NOT satisfy the gate (see _NON_REAL_PROVIDER_ADAPTER_NAMES in
    fulfilment.py) -- fake_test exists for local/dry-run testing, and a
    deploy with only this configured has no way to actually post a
    letter."""
    os.environ["LETTER_PROVIDER_PRIMARY"] = "fake_test"


def _clear_provider_config():
    for key in ("LETTER_PROVIDER_PRIMARY", "LETTER_PROVIDER_BACKUP_1", "LETTER_PROVIDER_BACKUP_2",
                "STANNP_API_KEY", "STANNP_TEMPLATE_ID"):
        os.environ.pop(key, None)


def _fully_ready_state():
    """The one combination that MUST turn the promise on -- all three
    independent conditions satisfied together. Used as the baseline for
    the "enabled journey" tests (Section 4, first pass) and as the
    starting point each invalid-combination test in Section 3 (second
    pass) knocks exactly one leg out from under."""
    _set_flag("true")
    _set_pipeline_fulfilment()
    _configure_real_provider()


class _EnvIsolation(unittest.TestCase):
    """Every test in this file must leave every gate-relevant env var
    exactly as it found it, whatever it sets mid-test -- other test files
    (and other tests in this file) must never see leaked state."""

    def setUp(self):
        self._had = {k: (k in os.environ) for k in _ALL_GATE_ENV_KEYS}
        self._old = {k: os.environ.get(k) for k in _ALL_GATE_ENV_KEYS}

    def tearDown(self):
        for k in _ALL_GATE_ENV_KEYS:
            if self._had[k]:
                os.environ[k] = self._old[k]
            else:
                os.environ.pop(k, None)


PLANS_WITH_LETTER_SUFFIX = [k for k, v in payments.PLANS.items() if v.get("letter_suffix")]
PLANS_WITHOUT_LETTER_SUFFIX = [k for k, v in payments.PLANS.items() if not v.get("letter_suffix")]


class TestDisabledJourneyDefault(_EnvIsolation):
    """LETTER_SENDING_LIVE unset -- the out-of-the-box, safe default."""

    def test_flag_reports_false_when_unset(self):
        _clear_flag()
        self.assertFalse(fulfilment.letter_sending_live())

    def test_no_plan_description_mentions_a_letter(self):
        _clear_flag()
        self.assertTrue(PLANS_WITH_LETTER_SUFFIX, "test setup sanity: at least one plan must have a letter_suffix")
        for key in payments.PLANS:
            desc = payments.plan_description(key)
            for fragment in _LETTER_SENTENCE_FRAGMENTS:
                self.assertNotIn(fragment, desc, f"plan_description({key!r}) leaked letter copy while disabled: {desc!r}")

    def test_no_plan_roi_mentions_a_letter(self):
        _clear_flag()
        for key in payments.PLANS:
            roi = payments.plan_roi(key)
            for fragment in _LETTER_SENTENCE_FRAGMENTS:
                self.assertNotIn(fragment, roi, f"plan_roi({key!r}) leaked letter copy while disabled: {roi!r}")

    def test_explicit_false_spellings_behave_identically_to_unset(self):
        for spelling in ("false", "0", "no", "off", ""):
            _set_flag(spelling)
            self.assertFalse(fulfilment.letter_sending_live(), f"spelling={spelling!r}")
            for key in PLANS_WITH_LETTER_SUFFIX:
                desc = payments.plan_description(key)
                self.assertNotIn("introduction letter", desc, f"spelling={spelling!r} key={key!r}")


class TestEnabledJourney(_EnvIsolation):
    """LETTER_SENDING_LIVE=true, pipeline='fulfilment', a real provider
    configured -- the flipped-on, real-sending-is-ready state (all three
    legs of the Section 3 second-pass gate)."""

    def test_flag_reports_true_for_truthy_spellings(self):
        _set_pipeline_fulfilment()
        _configure_real_provider()
        for spelling in ("1", "true", "True", "TRUE", "yes", "on"):
            _set_flag(spelling)
            self.assertTrue(fulfilment.letter_sending_live(), f"spelling={spelling!r}")

    def test_every_letter_plan_description_gains_the_suffix_exactly(self):
        _fully_ready_state()
        for key in PLANS_WITH_LETTER_SUFFIX:
            plan = payments.PLANS[key]
            expected = plan["description"] + plan["letter_suffix"]
            self.assertEqual(payments.plan_description(key), expected)

    def test_every_letter_plan_roi_gains_the_suffix_exactly(self):
        _fully_ready_state()
        for key in PLANS_WITH_LETTER_SUFFIX:
            plan = payments.PLANS[key]
            if not plan.get("roi_letter_suffix"):
                continue
            expected = plan["real_world_roi"] + plan["roi_letter_suffix"]
            self.assertEqual(payments.plan_roi(key), expected)

    def test_plans_with_no_letter_suffix_are_unaffected_by_the_flag(self):
        self.assertTrue(PLANS_WITHOUT_LETTER_SUFFIX, "test setup sanity: at least one plan must have no letter_suffix")
        _clear_flag()
        off_desc = {k: payments.plan_description(k) for k in PLANS_WITHOUT_LETTER_SUFFIX}
        off_roi = {k: payments.plan_roi(k) for k in PLANS_WITHOUT_LETTER_SUFFIX}
        _fully_ready_state()
        for k in PLANS_WITHOUT_LETTER_SUFFIX:
            self.assertEqual(payments.plan_description(k), off_desc[k], f"key={k!r}")
            self.assertEqual(payments.plan_roi(k), off_roi[k], f"key={k!r}")


class TestFreshReadPerCall(_EnvIsolation):
    """No caching -- matches the fulfilment.active_pipeline() precedent."""

    def test_flipping_env_var_mid_process_changes_the_very_next_call(self):
        # Pipeline + provider are ready throughout -- only the flag itself
        # flips, isolating that this specific leg is also read fresh every
        # call, not just active_pipeline() (already covered elsewhere).
        _set_pipeline_fulfilment()
        _configure_real_provider()
        _clear_flag()
        key = PLANS_WITH_LETTER_SUFFIX[0]
        before = payments.plan_description(key)
        self.assertNotIn("introduction letter", before)

        _set_flag("true")
        after_on = payments.plan_description(key)
        self.assertIn("introduction letter", after_on)

        _clear_flag()
        after_off_again = payments.plan_description(key)
        self.assertEqual(after_off_again, before)


class TestLiveSingleLeadPriceGoesThroughTheSameGate(_EnvIsolation):
    """Regression guard: _resolve_live_single_lead_price's synthesized dict
    must never read PLANS[...]["description"] directly -- it must call
    plan_description() like every other read site."""

    def _stub_live_lookup(self, plan_key="single_lead_small"):
        fake_database = types.ModuleType("database")
        fake_database.calculate_lead_freshness = MagicMock(return_value={
            "price": 19, "plan_key": plan_key,
        })
        fake_conn = MagicMock()
        fake_cur = MagicMock()
        fake_cur.fetchone.return_value = ("Fell one oak", "pending", "council_planning", None, None)
        fake_conn.cursor.return_value = fake_cur
        fake_database.get_db_conn = MagicMock(return_value=fake_conn)
        return fake_database

    def test_disabled_live_price_description_has_no_letter_copy(self):
        _clear_flag()
        with patch.dict(sys.modules, {"database": self._stub_live_lookup()}):
            result = payments._resolve_live_single_lead_price("lead-1")
        self.assertIsNotNone(result)
        self.assertNotIn("introduction letter", result["description"])

    def test_enabled_live_price_description_has_letter_copy(self):
        _fully_ready_state()
        with patch.dict(sys.modules, {"database": self._stub_live_lookup()}):
            result = payments._resolve_live_single_lead_price("lead-1")
        self.assertIsNotNone(result)
        self.assertIn("introduction letter", result["description"])


class TestCheckoutSessionProductDescriptionReflectsTheGate(_EnvIsolation):
    """create_checkout_session's Stripe product_description must reflect
    the flag for a subscription (static-plan) purchase -- exercised without
    actually reaching Stripe or the database (stripe.checkout.Session.create
    and every database.* call used on the subscription path are stubbed;
    lead_id is omitted so the live-price/reservation branch never runs)."""

    def setUp(self):
        super().setUp()
        self._orig_create = getattr(_fake_stripe.checkout, "Session", None)
        _fake_stripe.checkout.Session = MagicMock()
        _fake_stripe.checkout.Session.create.return_value = MagicMock(url="https://checkout.example/cs_test")

    def tearDown(self):
        super().tearDown()

    def _run_checkout(self, plan_key):
        return payments.create_checkout_session(plan_key)

    def test_disabled_subscription_checkout_has_no_letter_copy(self):
        _clear_flag()
        key = PLANS_WITH_LETTER_SUFFIX[0]
        # subscription-mode plan with a letter_suffix: "starter"
        self.assertEqual(payments.PLANS["starter"].get("mode"), "subscription")
        self._run_checkout("starter")
        _, kwargs = _fake_stripe.checkout.Session.create.call_args
        description = kwargs["line_items"][0]["price_data"]["product_data"]["description"]
        self.assertNotIn("introduction letter", description)

    def test_enabled_subscription_checkout_has_letter_copy(self):
        _fully_ready_state()
        self._run_checkout("starter")
        _, kwargs = _fake_stripe.checkout.Session.create.call_args
        description = kwargs["line_items"][0]["price_data"]["product_data"]["description"]
        self.assertIn("introduction letter", description)


class TestInvalidConfigurationCombinationsLeavePromiseOff(_EnvIsolation):
    """2026-09-18 review, Section 3 (second pass): "Couple the public
    posting promise to the correct fulfilment pipeline and a configured,
    enabled real provider. Keep explicit launch approval as well. Test
    invalid configuration combinations so a flag alone cannot advertise an
    unavailable service."

    Each test here starts from _fully_ready_state() (the one combination
    proven ON above) and knocks exactly one leg out, proving the promise
    goes back off -- i.e. no two-out-of-three combination is ever
    sufficient, in either direction (a technical leg missing, or the
    explicit approval itself missing)."""

    def test_flag_on_but_pipeline_still_legacy_is_off(self):
        """The exact scenario the instruction calls out: an operator (or a
        misconfigured deploy) sets LETTER_SENDING_LIVE=true while nothing
        has switched LETTER_DISPATCH_PIPELINE off 'legacy' -- the pipeline
        that actually runs the new provider-sending logic is inactive, so
        the flag alone must not advertise a service nothing is running."""
        _fully_ready_state()
        _set_pipeline_legacy()
        self.assertFalse(fulfilment.letter_sending_live())
        for key in PLANS_WITH_LETTER_SUFFIX:
            self.assertNotIn("introduction letter", payments.plan_description(key))

    def test_flag_on_but_pipeline_unset_defaults_to_legacy_is_off(self):
        """Same as above, but via the real default (unset -> 'legacy'),
        not an explicit 'legacy' -- the most likely real-world shape of
        this misconfiguration (a fresh deploy that never touched
        LETTER_DISPATCH_PIPELINE at all)."""
        _fully_ready_state()
        os.environ.pop(fulfilment.LETTER_DISPATCH_PIPELINE_ENV, None)
        self.assertFalse(fulfilment.letter_sending_live())

    def test_flag_on_correct_pipeline_but_no_provider_configured_is_off(self):
        """Pipeline is right, approval is given, but no LETTER_PROVIDER_*
        slot has anything configured -- nothing could ever accept a send."""
        _fully_ready_state()
        _clear_provider_config()
        self.assertFalse(fulfilment.letter_sending_live())
        for key in PLANS_WITH_LETTER_SUFFIX:
            self.assertNotIn("introduction letter", payments.plan_description(key))

    def test_flag_on_correct_pipeline_but_only_fake_provider_is_off(self):
        """A provider slot IS enabled and configured -- but it's fake_test,
        which exists for local/dry-run testing and can never actually post
        a letter. Must not count as 'a configured, enabled real
        provider'."""
        _fully_ready_state()
        _clear_provider_config()
        _configure_only_fake_provider()
        self.assertFalse(fulfilment.letter_sending_live())
        for key in PLANS_WITH_LETTER_SUFFIX:
            self.assertNotIn("introduction letter", payments.plan_description(key))

    def test_flag_on_correct_pipeline_real_provider_configured_but_disabled_is_off(self):
        """A real provider kind is named, but its own credentials are
        missing (is_configured() False) -- the slot exists but isn't
        actually usable, same as the provider registry itself would
        refuse to select it for a real send."""
        _fully_ready_state()
        os.environ.pop("STANNP_API_KEY", None)  # provider named, but incomplete -- not configured
        self.assertFalse(fulfilment.letter_sending_live())

    def test_pipeline_and_provider_ready_but_flag_left_off_is_off(self):
        """The reverse of every case above: technical readiness alone --
        correct pipeline, a real configured provider -- must NEVER be
        sufficient on its own. The explicit admin approval is still
        required, exactly as it was before this section's change; this is
        the regression guard for that half of the gate."""
        _set_pipeline_fulfilment()
        _configure_real_provider()
        _clear_flag()
        self.assertFalse(fulfilment.letter_sending_live())
        for key in PLANS_WITH_LETTER_SUFFIX:
            self.assertNotIn("introduction letter", payments.plan_description(key))

    def test_all_three_conditions_together_is_the_only_combination_that_is_on(self):
        """Positive control for the whole class -- proves the fixture
        itself (_fully_ready_state) really does turn the gate on, so the
        negative tests above are knocking a leg out from a genuinely-on
        baseline rather than one that was already off for some unrelated
        reason."""
        _fully_ready_state()
        self.assertTrue(fulfilment.letter_sending_live())


if __name__ == "__main__":
    unittest.main()
