"""
test_letter_promise_gate.py -- originally 2026-09-18 review, Section 4/
Section 3 (second pass): built to prove the public posting promise was
gated on executable configuration, not just comments. REWRITTEN 2026-09-24,
launch-experience rewrite (Nick's explicit ask: "TreeKey is not launched
yet. Build the finished launch experience now... Do not hide the product
explanation just because sending is disabled... Keep real payments and
real posting disabled through existing server-side controls").

That instruction is a deliberate, explicit reversal of the behaviour this
file used to test: the posted introduction is now described as the actual
launch product on every customer-facing surface, unconditionally --
payments.plan_description()/plan_roi() no longer read
fulfilment.letter_sending_live() at all (see payments.py's PLANS comment
and plan_description()'s own docstring). What still keeps a real customer
from being charged for a real send that won't happen is a separate,
existing pair of controls this file does NOT own: whichever
STRIPE_SECRET_KEY is deployed (test vs live -- outside this codebase) and
fulfilment.letter_sending_live() itself, which remains fully intact and
correctly gated (see below) for whenever it's wired back up to something
real (e.g. the actual dispatch pipeline, or a future admin "go live"
control) -- nothing currently calls it, but it is not dead code to delete,
it's a verified-correct gate kept ready for reuse.

Proves, with evidence:
  1. Every PLANS entry's plan_description()/plan_roi() includes its
     letter_suffix/roi_letter_suffix text UNCONDITIONALLY -- regardless of
     fulfilment.letter_sending_live()'s state (unset, explicitly false, or
     explicitly true with every leg configured). This is the regression
     guard for the exact behaviour this session's rewrite intentionally
     changed.
  2. _resolve_live_single_lead_price's synthesized "live" dict goes through
     the same always-on plan_description() call, not a raw
     PLANS[...]["description"] read (unchanged regression guard from the
     original file -- still a real risk: a future refactor could
     reintroduce a direct dict read that skips the suffix).
  3. create_checkout_session's Stripe product_description reflects the
     same always-on text.
  4. fulfilment.letter_sending_live() itself is untouched and still
     correct: requires all three of (a) the explicit LETTER_SENDING_LIVE
     flag, (b) active_pipeline() == 'fulfilment', (c) a real, enabled,
     configured (non-fake_test) provider -- fail-safe to False on any
     single leg missing, and reads fresh every call (no caching). Kept as
     its own coverage because nothing else in this test suite exercises
     these combinations directly, and the function is being kept
     deliberately intact for future reuse, not deleted.

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
    os.environ[fulfilment.LETTER_DISPATCH_PIPELINE_ENV] = "legacy"


def _configure_real_provider():
    os.environ["LETTER_PROVIDER_PRIMARY"] = "stannp"
    os.environ["STANNP_API_KEY"] = "sk_test_dummy_stannp_key"
    os.environ["STANNP_TEMPLATE_ID"] = "tmpl_dummy"


def _configure_only_fake_provider():
    os.environ["LETTER_PROVIDER_PRIMARY"] = "fake_test"


def _clear_provider_config():
    for key in ("LETTER_PROVIDER_PRIMARY", "LETTER_PROVIDER_BACKUP_1", "LETTER_PROVIDER_BACKUP_2",
                "STANNP_API_KEY", "STANNP_TEMPLATE_ID"):
        os.environ.pop(key, None)


def _fully_ready_state():
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


class TestPlanCopyAlwaysIncludesTheLetterPromise(_EnvIsolation):
    """2026-09-24, launch-experience rewrite: the posted-introduction
    explanation is core product copy now, not an optional suffix -- it
    must appear in plan_description()/plan_roi() regardless of
    fulfilment.letter_sending_live()'s state. Every PLANS entry (all 5
    subscription tiers, all 4 single-lead tiers) has a letter_suffix as of
    this rewrite -- test-setup sanity check guards against that silently
    regressing to "some plans forgotten" again."""

    def test_every_plan_has_a_letter_suffix(self):
        self.assertEqual(
            set(PLANS_WITH_LETTER_SUFFIX), set(payments.PLANS.keys()),
            "every plan should state the posted-introduction entitlement now -- "
            f"missing: {set(payments.PLANS.keys()) - set(PLANS_WITH_LETTER_SUFFIX)}"
        )

    def test_description_includes_letter_text_when_flag_unset(self):
        _clear_flag()
        for key in payments.PLANS:
            desc = payments.plan_description(key)
            self.assertTrue(
                any(fragment in desc for fragment in _LETTER_SENTENCE_FRAGMENTS),
                f"plan_description({key!r}) dropped its letter copy while the flag is unset: {desc!r}",
            )

    def test_description_includes_letter_text_when_flag_explicitly_false(self):
        for spelling in ("false", "0", "no", "off", ""):
            _set_flag(spelling)
            for key in payments.PLANS:
                desc = payments.plan_description(key)
                self.assertTrue(
                    any(fragment in desc for fragment in _LETTER_SENTENCE_FRAGMENTS),
                    f"spelling={spelling!r} key={key!r}: {desc!r}",
                )

    def test_description_and_roi_unaffected_by_fully_ready_state(self):
        """Flipping every leg of fulfilment.letter_sending_live() on must
        change nothing about the copy -- it was already showing the full
        text. Byte-for-byte identical proves there's no hidden second gate
        left over from the old design."""
        _clear_flag()
        off_desc = {k: payments.plan_description(k) for k in payments.PLANS}
        off_roi = {k: payments.plan_roi(k) for k in payments.PLANS}
        _fully_ready_state()
        for k in payments.PLANS:
            self.assertEqual(payments.plan_description(k), off_desc[k], f"key={k!r}")
            self.assertEqual(payments.plan_roi(k), off_roi[k], f"key={k!r}")

    def test_description_equals_base_plus_suffix_exactly(self):
        for key in PLANS_WITH_LETTER_SUFFIX:
            plan = payments.PLANS[key]
            expected = plan["description"] + plan["letter_suffix"]
            self.assertEqual(payments.plan_description(key), expected)

    def test_roi_equals_base_plus_suffix_exactly_where_present(self):
        for key in PLANS_WITH_LETTER_SUFFIX:
            plan = payments.PLANS[key]
            if not plan.get("roi_letter_suffix"):
                continue
            expected = plan["real_world_roi"] + plan["roi_letter_suffix"]
            self.assertEqual(payments.plan_roi(key), expected)


class TestLiveSingleLeadPriceGoesThroughTheSameCopyPath(_EnvIsolation):
    """Regression guard: _resolve_live_single_lead_price's synthesized dict
    must never read PLANS[...]["description"] directly -- it must call
    plan_description() like every other read site, so it always carries
    the letter text too."""

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

    def test_live_price_description_always_has_letter_copy(self):
        _clear_flag()
        with patch.dict(sys.modules, {"database": self._stub_live_lookup()}):
            result = payments._resolve_live_single_lead_price("lead-1")
        self.assertIsNotNone(result)
        self.assertIn("introduction letter", result["description"])


class TestCheckoutSessionProductDescriptionAlwaysHasTheLetterCopy(_EnvIsolation):
    """create_checkout_session's Stripe product_description must always
    include the letter text for a subscription (static-plan) purchase --
    exercised without actually reaching Stripe or the database
    (stripe.checkout.Session.create and every database.* call used on the
    subscription path are stubbed; lead_id is omitted so the live-price/
    reservation branch never runs)."""

    def setUp(self):
        super().setUp()
        self._orig_create = getattr(_fake_stripe.checkout, "Session", None)
        _fake_stripe.checkout.Session = MagicMock()
        _fake_stripe.checkout.Session.create.return_value = MagicMock(url="https://checkout.example/cs_test")

    def tearDown(self):
        super().tearDown()

    def _run_checkout(self, plan_key):
        return payments.create_checkout_session(plan_key)

    def test_subscription_checkout_has_letter_copy_regardless_of_flag(self):
        self.assertEqual(payments.PLANS["starter"].get("mode"), "subscription")
        for flag_state in (None, "false", "true"):
            if flag_state is None:
                _clear_flag()
            else:
                _set_flag(flag_state)
            self._run_checkout("starter")
            _, kwargs = _fake_stripe.checkout.Session.create.call_args
            description = kwargs["line_items"][0]["price_data"]["product_data"]["description"]
            self.assertIn("introduction letter", description, f"flag_state={flag_state!r}")


class TestLetterSendingLiveItselfRemainsCorrectlyGated(_EnvIsolation):
    """fulfilment.letter_sending_live() is kept fully intact -- nothing in
    this codebase currently calls it (payments.py/main.py/notifications.py
    no longer gate copy on it, and worker.py never did -- real send/dry-run
    is controlled separately), but it is a verified-correct gate kept
    ready for reuse (e.g. a future real go-live switch), not dead code to
    delete. This class is the ONLY remaining coverage of its combinatorics
    now that the copy-layer tests above no longer exercise it indirectly."""

    def test_flag_reports_false_when_unset(self):
        _clear_flag()
        self.assertFalse(fulfilment.letter_sending_live())

    def test_explicit_false_spellings_behave_identically_to_unset(self):
        for spelling in ("false", "0", "no", "off", ""):
            _set_flag(spelling)
            self.assertFalse(fulfilment.letter_sending_live(), f"spelling={spelling!r}")

    def test_flag_reports_true_for_truthy_spellings_when_fully_configured(self):
        _set_pipeline_fulfilment()
        _configure_real_provider()
        for spelling in ("1", "true", "True", "TRUE", "yes", "on"):
            _set_flag(spelling)
            self.assertTrue(fulfilment.letter_sending_live(), f"spelling={spelling!r}")

    def test_fresh_read_per_call_no_caching(self):
        _set_pipeline_fulfilment()
        _configure_real_provider()
        _clear_flag()
        self.assertFalse(fulfilment.letter_sending_live())
        _set_flag("true")
        self.assertTrue(fulfilment.letter_sending_live())
        _clear_flag()
        self.assertFalse(fulfilment.letter_sending_live())

    def test_flag_on_but_pipeline_still_legacy_is_off(self):
        _fully_ready_state()
        _set_pipeline_legacy()
        self.assertFalse(fulfilment.letter_sending_live())

    def test_flag_on_but_pipeline_unset_defaults_to_legacy_is_off(self):
        _fully_ready_state()
        os.environ.pop(fulfilment.LETTER_DISPATCH_PIPELINE_ENV, None)
        self.assertFalse(fulfilment.letter_sending_live())

    def test_flag_on_correct_pipeline_but_no_provider_configured_is_off(self):
        _fully_ready_state()
        _clear_provider_config()
        self.assertFalse(fulfilment.letter_sending_live())

    def test_flag_on_correct_pipeline_but_only_fake_provider_is_off(self):
        _fully_ready_state()
        _clear_provider_config()
        _configure_only_fake_provider()
        self.assertFalse(fulfilment.letter_sending_live())

    def test_flag_on_correct_pipeline_real_provider_configured_but_disabled_is_off(self):
        _fully_ready_state()
        os.environ.pop("STANNP_API_KEY", None)
        self.assertFalse(fulfilment.letter_sending_live())

    def test_pipeline_and_provider_ready_but_flag_left_off_is_off(self):
        _set_pipeline_fulfilment()
        _configure_real_provider()
        _clear_flag()
        self.assertFalse(fulfilment.letter_sending_live())

    def test_all_three_conditions_together_is_the_only_combination_that_is_on(self):
        _fully_ready_state()
        self.assertTrue(fulfilment.letter_sending_live())


if __name__ == "__main__":
    unittest.main()
