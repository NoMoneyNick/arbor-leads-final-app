#!/usr/bin/env python3
"""
tests/postgres_concurrency/run_first_offer_webhook_db_test.py

End-to-end check of the GBP 4.99 first introduction against a REAL disposable
PostgreSQL, with Stripe SIMULATED (no network, no credentials, no charge):

  create_checkout_session (real) -> fake Stripe Session.create captures exactly
  what would be charged -> a locally built checkout.session.completed event ->
  payments.handle_stripe_webhook (real) -> database.confirm_reserved_lead_sale
  and fulfilment.create_allocation_and_obligation (real).

It proves what the code does with a well-formed event. It does NOT prove that
Stripe itself accepts the session or signs/delivers the event -- that needs a
real Stripe TEST-mode run (see the handoff). Letter sending stays disabled: no
provider is called and LETTER_SENDING_LIVE is never set.

Exit codes: 0 all passed, 1 a check failed, 2 PostgreSQL unavailable.
"""
from __future__ import annotations

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path[:0] = [APP, HERE]
for extra in (os.path.join(os.path.dirname(APP), "extra_modules"), "/mnt/user-data/uploads/VECTOR DATA LABS"):
    if os.path.isdir(extra):
        sys.path.append(extra)

import run_concurrency_tests as rct  # noqa: E402
import run_signup_real_db_tests as base  # noqa: E402

DB = base.DB = "treekey_first_offer_webhook"


def main() -> int:
    try:
        pg_bin = rct.find_pg_bin()
    except FileNotFoundError:
        print("PostgreSQL not available: NOT run.")
        return 2
    from types import SimpleNamespace
    from unittest.mock import MagicMock
    os.environ["LETTER_DISPATCH_PIPELINE"] = "fulfilment"
    os.environ.pop("LETTER_SENDING_LIVE", None)
    for n in ("psycopg2", "psycopg2.extras", "psycopg2.pool", "psycopg2.errors", "psycopg2.extensions"):
        sys.modules[n] = MagicMock()

    created = []

    class _Sess:
        @staticmethod
        def create(**kw):
            created.append(kw)
            return SimpleNamespace(id=f"cs_test_sim_{len(created)}", url="https://checkout.stripe.test/sim")

    fake_stripe = types.ModuleType("stripe")
    fake_stripe.api_key = "sk_test_SIMULATED"
    fake_stripe.error = SimpleNamespace(AuthenticationError=type("A", (Exception,), {}), StripeError=type("S", (Exception,), {}),
                                        SignatureVerificationError=type("V", (Exception,), {}))
    fake_stripe.checkout = SimpleNamespace(Session=_Sess)
    fake_stripe.Refund = MagicMock()
    fake_stripe.Webhook = MagicMock()
    sys.modules["stripe"] = fake_stripe
    notif = types.ModuleType("notifications")
    notif.send_purchased_lead_email = MagicMock()
    notif.send_system_incident_alert = MagicMock()
    sys.modules["notifications"] = notif

    for _ in range(20):  # scanners (real) imports modules that are not part of this checkout; stub only those
        try:
            import database, scanners, payments, fulfilment, letter_content  # noqa: F401
            break
        except ModuleNotFoundError as e:
            sys.modules[e.name] = MagicMock()
            for m in ("database", "scanners", "payments", "fulfilment", "letter_content"):
                sys.modules.pop(m, None)
    payments.STRIPE_WEBHOOK_SECRET = "whsec_SIMULATED"
    payments.stripe.api_key = "sk_test_SIMULATED"

    cluster = rct.Cluster(pg_bin, rct.PG_OS_USER_DEFAULT)
    results, conns = [], []

    def new_conn():
        c = base.PsqlConn(cluster)
        conns.append(c)
        return c

    def check(name, cond, detail=""):
        results.append((name, bool(cond), detail))
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f"  -- {detail}" if detail and not cond else ""))

    try:
        cluster.start()
        cluster.createdb(DB)
        cluster.psql("""
            CREATE EXTENSION IF NOT EXISTS pgcrypto;
            CREATE TABLE leads (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), reference TEXT UNIQUE, address TEXT,
                summary TEXT, council_source TEXT, lead_score INT DEFAULT 50, lead_price INT DEFAULT 29,
                applicant_name TEXT, agent_name TEXT, agent_company TEXT, has_agent BOOLEAN, registered_date DATE,
                status TEXT DEFAULT 'new', reserved_by_email TEXT, reserved_session_id TEXT, reserved_at TIMESTAMPTZ,
                planning_status TEXT DEFAULT 'pending', lead_source_type TEXT DEFAULT 'council_planning',
                discovered_at TIMESTAMPTZ DEFAULT NOW(), tags TEXT[] DEFAULT '{}');
            CREATE TABLE payments (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), stripe_session_id TEXT UNIQUE,
                plan TEXT, amount_pence INT, customer_email TEXT, status TEXT DEFAULT 'pending',
                created_at TIMESTAMPTZ DEFAULT NOW(), lead_id TEXT, account_email TEXT, fulfillment_outcome TEXT,
                updated_at TIMESTAMPTZ DEFAULT NOW(), offer_kind TEXT, offer_phone_key TEXT, offer_business_key TEXT);
            CREATE TABLE contractor_subscriptions (id SERIAL PRIMARY KEY, customer_email TEXT, active BOOLEAN DEFAULT TRUE);
            CREATE TABLE limbo_accounts (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), email TEXT UNIQUE NOT NULL, center_outcode TEXT NOT NULL);
            CREATE TABLE lead_dispatches (id SERIAL PRIMARY KEY, contractor_email TEXT);
        """, db=DB)
        boot = new_conn()
        cur = boot.cursor()
        letter_content.init_letter_content_schema(cur)
        fulfilment.init_fulfilment_schema(cur)
        boot.commit()
        database.SURL = "scratch"
        database.get_db_conn = new_conn

        def q(sql, params=None):
            c = new_conn()
            cu = c.cursor()
            cu.execute(sql, params)
            rows = list(cu.rows)
            c.commit()
            return rows

        def add_lead(ref, summary):
            q("INSERT INTO leads (reference, address, summary, council_source, applicant_name, discovered_at) "
              "VALUES (%s, '1 Test Road, Leeds LS1 1AA', %s, 'Leeds', 'Mr Test', NOW())", (ref, summary))
            return q("SELECT id::text FROM leads WHERE reference=%s", (ref,))[0][0]

        def event(session_id, params, event_id):
            md = dict(params["metadata"])
            return {"id": event_id, "type": "checkout.session.completed", "data": {"object": {
                "id": session_id, "customer_details": {"email": "dave@apex-trees.co.uk"},
                "amount_total": params["line_items"][0]["price_data"]["unit_amount"], "metadata": md,
                "client_reference_id": params.get("client_reference_id"), "payment_intent": "pi_test_sim"}}}

        def deliver(ev):
            payments.stripe.Webhook.construct_event = MagicMock(return_value=ev)
            return payments.handle_stripe_webhook(b"{}", "t=0,v1=simulated")

        email = "dave@apex-trees.co.uk"
        c = new_conn(); cu = c.cursor()
        letter_content.upsert_contractor_settings(cu, letter_content.ContractorLetterSettings(
            contractor_email=email, business_name="Apex Trees Ltd", phone="01234 567890"))
        c.commit()

        std = add_lead("REF-STD-1", "Fell one oak tree in rear garden")
        pri = add_lead("REF-PRI-1", "Removal of 40 trees for development of a large commercial site")
        std2 = add_lead("REF-STD-2", "T1 Ash - reduce crown by 20%")

        check("eligible before any purchase", database.first_offer_status(email)["eligible"] is True)

        # Priority listing: refused, nothing reserved or created
        try:
            payments.create_checkout_session("single_lead_medium", "GB", pri, account_email=email, first_offer=True)
            refused = None
        except payments.FirstOfferUnavailable as e:
            refused = e.reason
        check("a Priority listing is refused for the offer, no Stripe session, lead untouched",
              refused == "not_standard" and not created and q("SELECT status FROM leads WHERE id::text=%s", (pri,))[0][0] == "new")

        # Standard listing: checkout session
        url = payments.create_checkout_session("single_lead_medium", "GB", std, account_email=email, first_offer=True)
        check("standard listing creates a (simulated) Stripe session", url == "https://checkout.stripe.test/sim" and len(created) == 1)
        params = created[0]
        check("session would charge exactly 499 pence GBP, no promo codes",
              params["line_items"][0]["price_data"]["unit_amount"] == 499 and params["line_items"][0]["price_data"]["currency"] == "gbp"
              and params["allow_promotion_codes"] is False and params["metadata"].get("first_offer") == "1")
        token = params["metadata"]["reservation_token"]
        row = q("SELECT status, amount_pence, offer_kind FROM payments WHERE stripe_session_id=%s", (token,))[0]
        check("pending offer order recorded with the offer marker", row == ("pending", "499", "first_introduction"), str(row))
        check("lead reserved to this checkout", q("SELECT status, reserved_session_id FROM leads WHERE id::text=%s", (std,))[0] == ("reserved", token))
        check("eligibility now held (offer_in_progress)", database.first_offer_status(email)["reason"] == "offer_in_progress")

        # webhook
        ev = event("cs_test_sim_1", params, "evt_sim_1")
        out = deliver(ev)
        check("webhook handled without error", not (isinstance(out, dict) and out.get("error")), str(out))
        row = q("SELECT status, fulfillment_outcome, amount_pence FROM payments WHERE stripe_session_id=%s", (token,))[0]
        check("payment recorded as paid/fulfilled at 499", row == ("paid", "fulfilled", "499"), str(row))
        check("lead is now sold (claimed)", q("SELECT status FROM leads WHERE id::text=%s", (std,))[0][0] == "claimed")
        alloc = q("SELECT buyer_email, allocation_type, source_payment_ref FROM lead_allocations")
        check("one allocation created for the buyer", alloc == [(email, "single_purchase", token)], str(alloc))
        ob = q("SELECT status FROM letter_obligations")
        print("   obligation status:", ob)
        check("one letter obligation created, not dispatched/sent", len(ob) == 1 and ob[0][0] in ("pending_approval", "pending_funding", "ready", "blocked_missing_data"), str(ob))
        check("no letter reached the provider (sending disabled)", not fulfilment.letter_sending_live()
              and q("SELECT COUNT(*) FROM letter_obligations WHERE status IN ('submitting','provider_accepted','dispatched','delivered')")[0][0] == "0")
        check("purchase email requested once", notif.send_purchased_lead_email.call_count == 1)

        # eligibility consumed exactly once
        check("offer now consumed", database.first_offer_status(email)["reason"] == "offer_used")
        # replay of the same event: no duplicates
        deliver(ev)
        check("replayed webhook creates no second allocation/obligation and stays one paid order",
              q("SELECT COUNT(*) FROM lead_allocations")[0][0] == "1" and q("SELECT COUNT(*) FROM letter_obligations")[0][0] == "1"
              and q("SELECT COUNT(*) FROM payments WHERE status='paid'")[0][0] == "1")
        # second attempt refused; normal price after
        try:
            payments.create_checkout_session("single_lead_medium", "GB", std2, account_email=email, first_offer=True)
            second = None
        except payments.FirstOfferUnavailable as e:
            second = e.reason
        check("second offer attempt refused (offer_used), lead not reserved", second == "offer_used"
              and q("SELECT status FROM leads WHERE id::text=%s", (std2,))[0][0] == "new")
        n_before = len(created)
        url2 = payments.create_checkout_session("single_lead_medium", "GB", std2, account_email=email)
        check("a later ordinary purchase is charged the normal price", len(created) == n_before + 1
              and created[-1]["line_items"][0]["price_data"]["unit_amount"] in (1900, 2900) and "first_offer" not in created[-1]["metadata"])
    finally:
        for c in conns:
            c.close()
        cluster.cleanup()

    failed = [r for r in results if not r[1]]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed.")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
