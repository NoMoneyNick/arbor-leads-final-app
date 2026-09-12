import os
import logging
from typing import Optional, Dict, Any
import stripe
from dotenv import load_dotenv

load_dotenv()
stripe.api_key = os.getenv("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "").strip()
PUBLIC_APP_URL = os.getenv("PUBLIC_APP_URL", "").strip().rstrip("/")
logger = logging.getLogger("vector-data-labs")

# Best-effort in-process dedup for retried Stripe webhooks (Stripe redelivers the same
# event id on timeout/non-2xx — that's a normal retry, not a fraud/race signal). Doesn't
# survive a restart or a multi-instance deploy, but removes the common false-positive case.
#
# Aug 30 2026: this used to be one function that BOTH checked and marked an event id as
# processed in the same call, at the very top of handle_stripe_webhook -- before any
# actual fulfillment (database.burn_lead_inventory, etc.) had even been attempted. That
# meant a genuinely failed first attempt (e.g. a transient DB error while burning a lead)
# still marked the event id as "seen", so if Stripe retried the SAME webhook, the retry
# was wrongly classified as "just a duplicate delivery of an already-handled event" --
# silently skipping fulfillment a second time AND suppressing the CRITICAL "double sale"
# alert that exists specifically to catch a customer paying and not receiving their lead.
# Split into a peek (no side effect, used for the "is this worth alerting about" check)
# and an explicit mark-as-fulfilled call made ONLY after real success, so a failed first
# attempt is retried honestly instead of being swallowed.
_PROCESSED_STRIPE_EVENT_IDS: Dict[str, float] = {}


def _seen_stripe_event(event_id: str) -> bool:
    """Peek only -- does NOT mark the event as processed. Safe to call before
    fulfillment has been attempted."""
    import time
    now = time.time()
    for k in [k for k, t in _PROCESSED_STRIPE_EVENT_IDS.items() if now - t > 86400]:
        del _PROCESSED_STRIPE_EVENT_IDS[k]
    if not event_id:
        return False
    return event_id in _PROCESSED_STRIPE_EVENT_IDS


def _mark_stripe_event_fulfilled(event_id: str) -> None:
    """Call ONLY after the event's action has actually succeeded (lead burned,
    subscription registered, etc.) -- marks it so a Stripe redelivery of this
    same event id is correctly recognised as a harmless duplicate, not retried."""
    if event_id:
        import time
        _PROCESSED_STRIPE_EVENT_IDS[event_id] = time.time()

# ── Pricing Plans (5 Tailored Packages + Single Purchase Marketplace) ─────────
# All amounts in pence (GBP)
PLANS = {
    "stump_pro": {
        "name": "TreeKey Stump Pro",
        "description": "Dedicated to stump grinding contractors. Filtered exclusively for felling & stump removal applications with gate clearance checks.",
        "amount": 2900,   # £29/month
        "mode": "subscription",
        "badge": "Stump Specialists",
        "real_world_roi": "One £150 stump job per month gives a 5x ROI."
    },
    "climber_domestic": {
        "name": "TreeKey Climber (Domestic)",
        "description": "For 1-2 van tree surgeons. Daily domestic crown reductions, pollards, garden felling, 1-tap homeowner letters, and Street View briefs.",
        "amount": 4900,   # £49/month
        "mode": "subscription",
        "badge": "Most Popular",
        "real_world_roi": "Less than half a tank of diesel (£49/mo). One £400 job every 6 months pays for the entire year with a 4x net return."
    },
    "arb_consultant": {
        "name": "TreeKey Consultant (Planning & Survey)",
        # Aug 30 2026: dropped "direct developer company contacts" -- no code
        # path in this project actually looks up or delivers a developer's
        # contact details (that would need Companies House enrichment wired
        # to a lead's applicant, which doesn't exist here; bulk_contractor_
        # extractor.py's Companies House lookups are for a separate pipeline
        # that finds tree-surgery companies to sell subscriptions to, not
        # for enriching a lead's developer applicant). Only the two
        # deliverables the pipeline actually produces are listed.
        "description": "For qualified arborists (TechArb/MICFor). Developer condition 7 discharges and BS5837 impact assessment planning intelligence.",
        "amount": 8900,   # £89/month
        "mode": "subscription",
        "badge": "Planning & Surveyors",
        "real_world_roi": "One £800 developer method statement report every 3 months gives a 3x ROI on pure desktop work."
    },
    "commercial_forestry": {
        "name": "TreeKey Commercial & Forestry",
        "description": "For heavy machinery operators & commercial outfits. Multi-tree site clearances (3+), Ash Dieback blocks, and B2B institutional tenders.",
        "amount": 13900,  # £139/month
        "mode": "subscription",
        "badge": "Heavy Commercial",
        "real_world_roi": "One commercial job won per year (£3,000–£15,000) covers your subscription for 2–5 years."
    },
    "treekey_elite": {
        "name": "TreeKey Elite (All-Access Partner)",
        # Sep 12 2026, Nick's explicit ask ("remove whatsapp feature and
        # letter sending feature off anything public until its built and
        # deployed... including any statements that are untrue"): this
        # description previously promised three things that don't exist in
        # the codebase at all -- "Zero-Minute Instant WhatsApp Alerts" (the
        # real mechanism is a forward-to-yourself link inside the normal
        # email, not push delivery, and isn't even Elite-exclusive --
        # notifications.py's own comments say so), "RAMS Legal Pack" (no
        # RAMS generator exists anywhere -- it's on the未-built backlog),
        # and "Automated Direct Mailouts" (no letter-sending code exists at
        # all -- it's on the not-yet-built backlog). Reworded to only claim
        # what this tier actually, verifiably does today: full category
        # access at the widest radius, and top-priority dispatch
        # (TIER_PRIORITY is real and does put Elite ahead of lower tiers in
        # the dispatch order). Add real features back to this line only
        # once they're actually built and deployed, never before.
        "description": "100% Unrestricted Access to ALL categories across 30 miles + top-priority lead dispatch, ahead of every lower tier.",
        "amount": 17900,  # £179/month
        "mode": "subscription",
        "badge": "VIP All-Access",
        "real_world_roi": "Complete business operating system. First look at every new lead in your category before lower tiers see it."
    },
    # Homepage general-ledger tiers (radius-based, distinct from the tailored tiers above)
    "sole_trader": {
        "name": "TreeKey Sole Trader",
        "description": "Perfect for one-man bands and local startups aiming to grow steadily.",
        "amount": 4900,   # £49/month
        "mode": "subscription",
        "badge": "Sole Trader",
        "real_world_roi": "One job pays for the month."
    },
    "commercial_pro": {
        "name": "TreeKey Commercial Pro",
        "description": "The sweet spot for established 3-man crews hunting lucrative clearances.",
        "amount": 14900,  # £149/month
        "mode": "subscription",
        "badge": "Best for Crews",  # was "Most Popular" — duplicated climber_domestic's badge on the same pricing page
        # Aug 30 2026: was "The average commercial site clearance pays
        # £2,500+" -- stated as a verified average with no source behind it,
        # the same pattern as the fabricated lead-count stats fixed
        # elsewhere this pass. Reworded to match the hypothetical framing
        # used by every other tier's real_world_roi (an illustrative "if you
        # land one job at £X" calculation, not an asserted statistic).
        "real_world_roi": "One commercial site clearance landed at £2,500 pays for the year."
    },
    "regional_elite": {
        "name": "TreeKey Regional Elite",
        "description": "For massive operations running multiple crews across a wide geographic spread.",
        "amount": 29900,  # £299/month
        "mode": "subscription",
        "badge": "Regional Elite",
        # Sep 12 2026, same pass as treekey_elite above: "a dedicated
        # account manager" isn't real -- there's no support-staff/account-
        # management system anywhere in this codebase, and nothing to make
        # one true (a solo non-technical founder can't dedicate a named
        # person to each Regional Elite subscriber). Dropped rather than
        # reworded, per the same "remove until built and deployed" rule.
        # The radius and priority-routing claims stay -- both real (
        # TIER_MAX_RADIUS=50 for this tier, TIER_PRIORITY dispatch weighting).
        "real_world_roi": "50-mile radial boundary with first-priority API routing."
    },
    # Single Lead Pay-As-You-Go Purchases (Single-Sale Inventory Burn)
    # Aug 30 2026: all three single-lead tiers previously promised "homeowner
    # name" as a guaranteed instant unlock, and the large tier promised
    # "developer contact" outright. Neither is accurate: UK councils never
    # publish a phone number or email on a planning application (a privacy-
    # law limit, not a scraper gap -- confirmed by auditing what the
    # homeowner_contact field actually contained across 1,085 real leads:
    # only placeholder/test data), and the applicant name is only present
    # when the council itself chose to record and publish it -- it isn't on
    # every application. Reworded to promise only what's actually, reliably
    # delivered: the address and application details, with the applicant
    # name and agent status shown *when the council recorded them* (see
    # notifications.py's send_purchased_lead_email, which now says exactly
    # that on a per-lead basis rather than promising it here as a given).
    "single_lead_small": {
        "name": "Single Lead Unlock (Domestic Maintenance)",
        "description": "100% Exclusive unshared planning lead. Once purchased, this lead is permanently deleted from all systems and never sold again.",
        "amount": 1900,   # £19 one-off
        "mode": "payment",
        "badge": "Single Purchase",
        "real_world_roi": "Instant unlocked property address and application details, plus a Street View brief. Applicant name included when the council has published one."
    },
    "single_lead_medium": {
        "name": "Single Lead Unlock (Standard Felling / Tree Removal)",
        "description": "100% Exclusive unshared felling lead. Permanently burned from inventory upon purchase.",
        "amount": 2900,   # £29 one-off
        "mode": "payment",
        "badge": "Single Purchase",
        "real_world_roi": "Instant unlocked property address and application details, plus a Street View brief. Applicant name included when the council has published one."
    },
    "single_lead_large": {
        "name": "Single Lead Unlock (Commercial / Site Clearance / TPO)",
        "description": "100% Exclusive high-value commercial or developer planning lead. Burned from inventory immediately upon purchase.",
        "amount": 4900,   # £49 one-off
        "mode": "payment",
        "badge": "Single Purchase",
        "real_world_roi": "Instant unlocked property address and full planning specs. Applicant name included when the council has published one."
    }
}


def _resolve_live_single_lead_price(lead_id: str) -> Optional[dict]:
    """Sep 12 2026, Nick's explicit ask ("everything... adjusted
    automatically in real time... that way a price decay would work IF
    that's what we choose"): computes the exact amount to charge for a
    single-lead checkout directly from THAT lead's current row, via the
    same database.calculate_lead_freshness() the marketplace card itself
    uses to display a price. Previously the charged amount came from a
    static plan_key -> Stripe price_data lookup (the freshness/checkout
    mismatch bug fixed earlier today made those two numbers agree, but
    both were still frozen at whatever they were when the page rendered).
    This closes the gap all the way: the price is recalculated fresh at
    the literal moment someone clicks buy, straight from the database, so
    there's no snapshot anywhere to go stale, and it's also the one piece
    of plumbing a future genuine decay-price or value-tier-based pricing
    model needs -- once that's decided, it changes what this function
    returns, not any Stripe-side configuration.

    Deliberately fails CLOSED, not open: if the lead can't be found, has
    already been sold, or the DB is unreachable, this returns None and the
    caller refuses the whole checkout (existing "Payment System
    Unavailable" page) rather than falling back to a possibly-wrong static
    price -- a failed checkout the customer can retry is a far smaller
    problem than charging an amount that doesn't match what's actually for
    sale, which is the exact class of bug this whole pass exists to kill."""
    try:
        import database
        conn = database.get_db_conn()
        cur = conn.cursor()
        try:
            cur.execute("""
                SELECT summary, status, lead_source_type, discovered_at, registered_date
                FROM leads WHERE id = %s;
            """, (lead_id,))
            row = cur.fetchone()
        finally:
            cur.close()
            conn.close()
        if not row:
            logger.warning(f"[Stripe] Live price lookup: lead_id={lead_id} not found (deleted or already sold).")
            return None
        summary, status, source_type, discovered_at, registered_date = row
        fresh = database.calculate_lead_freshness(
            discovered_at, planning_status=status or "pending", summary=summary or "",
            source_type=source_type or "council_planning", registered_date=registered_date
        )
        price_pounds = fresh.get("price", 0) or 0
        if price_pounds <= 0:
            logger.warning(f"[Stripe] Live price lookup: lead_id={lead_id} is expired/zero-price -- refusing checkout.")
            return None
        # Sep 12 2026: also re-resolve which plan_key applies RIGHT NOW (not
        # whichever one was embedded in the checkout link at page-load
        # time), so the Stripe product name/description shown to the
        # customer can never disagree with the amount they're actually
        # charged, even if they sat on the page long enough to cross a
        # freshness-tier boundary (e.g. Flash Hot -> Active).
        live_plan_key = fresh.get("plan_key")
        live_plan = PLANS.get(live_plan_key) if live_plan_key else None
        return {
            "amount_pence": int(price_pounds) * 100,
            "plan_key": live_plan_key,
            "name": live_plan["name"] if live_plan else "Single Lead Unlock",
            "description": live_plan["description"] if live_plan else "Exclusive planning lead.",
        }
    except Exception as e:
        logger.error(f"[Stripe] Live price lookup failed for lead_id={lead_id}: {e}")
        return None


def create_checkout_session(plan_key: str, outcode: str = None, lead_id: str = None, radius: int = 15,
                             full_postcode: str = None, job_size: str = None) -> Optional[str]:
    """
    Creates a Stripe Checkout session for the given plan or single lead purchase.
    Returns the checkout URL to redirect the customer to.
    """
    if not stripe.api_key:
        logger.error("[Stripe] STRIPE_SECRET_KEY is not set.")
        return None

    plan = PLANS.get(plan_key)
    if not plan:
        logger.error(f"[Stripe] Unknown plan: {plan_key}")
        return None

    try:
        unit_amount = plan["amount"]
        product_name = plan["name"]
        product_description = plan["description"]
        if lead_id:
            # Single-lead purchase: charge the live price (and use the live
            # product name/description), not whatever was static/embedded
            # in the checkout link at page-load time -- see
            # _resolve_live_single_lead_price's own docstring. Refuse the
            # whole checkout on any failure rather than silently falling
            # back to a possibly stale or wrong static amount.
            live = _resolve_live_single_lead_price(lead_id)
            if live is None:
                logger.error(f"[Stripe] Refusing checkout for lead_id={lead_id} -- no valid live price available.")
                return None
            unit_amount = live["amount_pence"]
            product_name = live["name"]
            product_description = live["description"]

        price_data = {
            "currency": "gbp",
            "product_data": {"name": product_name, "description": product_description},
            "unit_amount": unit_amount,
        }
        if plan["mode"] == "subscription":
            price_data["recurring"] = {"interval": "month"}

        session_params = {
            "line_items": [{"price_data": price_data, "quantity": 1}],
            "mode": plan["mode"],
            "success_url": f"{PUBLIC_APP_URL}/payment/success?session_id={{CHECKOUT_SESSION_ID}}",
            "cancel_url": f"{PUBLIC_APP_URL}/pricing",
            "allow_promotion_codes": True,
            "metadata": {"plan_key": plan_key, "tier": plan_key}
        }

        if plan["mode"] == "subscription":
            # Carry plan_key on the Subscription object itself (not just the Checkout
            # Session), so later subscription-level webhook events still have it.
            session_params["subscription_data"] = {"metadata": {"plan_key": plan_key, "tier": plan_key}}

        if outcode:
            session_params["client_reference_id"] = outcode
            session_params["metadata"]["outcode"] = outcode
            session_params["metadata"]["radius_miles"] = str(radius)

        # Sep 8 2026, proximity-system rework: carry the customer's raw
        # postcode input (may be a full postcode, more precise than the
        # bare outcode above) and job-size preference through Stripe
        # metadata so the webhook can pass them straight to
        # database.register_or_update_subscription. Both optional --
        # nothing here changes for a customer who only gives an outcode.
        if full_postcode:
            session_params["metadata"]["full_postcode"] = full_postcode
        if job_size:
            session_params["metadata"]["job_size"] = job_size

        if lead_id:
            session_params["metadata"]["lead_id"] = lead_id

        session = stripe.checkout.Session.create(**session_params)
        logger.info(f"[Stripe] Checkout session created for plan '{plan_key}' outcode={outcode} radius={radius}mi (lead_id={lead_id}): {session.id}")
        return session.url

    except stripe.error.AuthenticationError:
        logger.error("[Stripe] Invalid API key.")
        # Sep 3 2026: found during the "predict future issues, even ones
        # that have never errored" audit -- this is the single most
        # expensive silent-failure path in the whole app. This function IS
        # the customer checkout button. If STRIPE_SECRET_KEY ever goes
        # invalid (revoked, rotated in Stripe but not updated in Render,
        # accidentally swapped for a test-mode key), every single customer
        # trying to pay gets a silently-failed checkout (this just returns
        # None) with nothing ever alerting a human -- revenue could stop
        # completely and the only symptom would be a customer complaining,
        # or nobody noticing at all. The webhook side of this file already
        # alerts on its own auth/signature failures; this is the matching
        # fix for the checkout-creation side.
        import notifications
        notifications.send_system_incident_alert(
            category="SECURITY & API KEYS",
            title="STRIPE API KEY INVALID -- CHECKOUT IS DOWN",
            description="CRITICAL: Stripe rejected checkout-session creation with an authentication error -- STRIPE_SECRET_KEY is invalid, revoked, or mismatched (e.g. a live key swapped for test, or vice versa).",
            impact="No customer can complete a purchase right now -- every checkout attempt is silently failing.",
            action_required="Check STRIPE_SECRET_KEY in Render against the current key in the Stripe Dashboard (API keys page). Confirm live/test mode matches.",
            severity="CRITICAL",
            throttle_hours=1.0
        )
    except stripe.error.StripeError as e:
        logger.error(f"[Stripe] API error: {e}")
    except Exception as e:
        logger.error(f"[Stripe] Unexpected error: {e}", exc_info=True)
    return None


def handle_stripe_webhook(payload: bytes, sig_header: str) -> dict:
    """
    Verifies and processes an incoming Stripe webhook event.
    Returns a dict with the event type and relevant data.
    """
    import notifications

    if not STRIPE_WEBHOOK_SECRET:
        logger.error("[Stripe Webhook] STRIPE_WEBHOOK_SECRET is not set.")
        notifications.send_system_incident_alert(
            category="SECURITY & PAYMENTS",
            title="STRIPE_WEBHOOK_SECRET NOT SET IN ENVIRONMENT",
            description="CRITICAL: The Stripe Webhook listener received an event but STRIPE_WEBHOOK_SECRET is missing.",
            impact="Subscription activations and one-off lead credit purchases cannot be fulfilled automatically.",
            action_required="Add STRIPE_WEBHOOK_SECRET from your Stripe Dashboard (Developers > Webhooks) to Render Environment Settings.",
            severity="CRITICAL",
            throttle_hours=4.0
        )
        return {"error": "Webhook secret not configured"}

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, STRIPE_WEBHOOK_SECRET)
    except stripe.error.SignatureVerificationError:
        logger.warning("[Stripe Webhook] Invalid signature — possible spoofed request.")
        # Sep 4 2026: bumped from WARNING to CRITICAL. notifications.
        # send_system_incident_alert()'s WARNING tier now logs-and-waits-
        # for-recurrence instead of emailing immediately (see that
        # function's own Sep 4 comment) -- right for a benign, low-stakes
        # scraper quirk, wrong here: a broken STRIPE_WEBHOOK_SECRET means
        # every real payment silently fails to fulfil, and a genuine
        # spoofed request is a live security event. Neither should sit
        # unreported for up to 3 days waiting to "recur".
        notifications.send_system_incident_alert(
            category="SECURITY & PAYMENTS",
            title="STRIPE WEBHOOK SIGNATURE MISMATCH / INVALID SIGNATURE",
            description="An incoming webhook payload failed cryptographic signature verification against STRIPE_WEBHOOK_SECRET.",
            impact="The webhook was rejected to protect against spoofed transactions. If this was a legitimate Stripe event, webhook fulfillment failed.",
            action_required="1. Verify STRIPE_WEBHOOK_SECRET in Render matches the Signing Secret in Stripe Dashboard. 2. Check if a new webhook endpoint was created in Stripe.",
            severity="CRITICAL",
            throttle_hours=2.0
        )
        return {"error": "Invalid signature"}
    except Exception as e:
        logger.error(f"[Stripe Webhook] Parse error: {e}")
        return {"error": str(e)}

    event_type = event["type"]
    data = event["data"]["object"]
    event_id = event.get("id")
    is_retry = _seen_stripe_event(event_id)

    # GDPR Masking for logs
    mask = lambda e: f"{e[0]}***@{e.split('@')[1]}" if e and '@' in e else "unknown"

    if event_type == "checkout.session.completed":
        session_id = data.get("id")
        customer_email = data.get("customer_details", {}).get("email", "unknown")
        amount = data.get("amount_total", 0)
        metadata = data.get("metadata", {})
        outcode = data.get("client_reference_id") or metadata.get("outcode")
        lead_id = metadata.get("lead_id")
        
        import database
        # 1. If this was a single lead purchase, execute the Single-Sale Inventory Burn
        if lead_id:
            lead_data = database.burn_lead_inventory(lead_id, customer_email)
            if lead_data:
                logger.info(f"[Stripe] Lead {lead_id} burned from inventory for {mask(customer_email)}")
                notifications.send_purchased_lead_email(customer_email, lead_data)
                _mark_stripe_event_fulfilled(event_id)
            elif is_retry:
                # Stripe redelivers the same event on timeout/non-2xx — this is an
                # expected retry of an already-fulfilled purchase, not a real double-sale.
                logger.info(f"[Stripe] Duplicate webhook delivery for already-processed lead {lead_id} ({mask(customer_email)}) — ignoring retry.")
            else:
                logger.warning(f"[Stripe] Lead {lead_id} was already claimed or not found, but {mask(customer_email)} paid for it!")
                notifications.send_system_incident_alert(
                    category="REVENUE & BILLING",
                    title=f"DOUBLE SALE RACE CONDITION: {mask(customer_email)} paid for already-claimed lead!",
                    description=f"Customer {customer_email} paid £{amount / 100:.2f} for lead {lead_id}, but the lead was already claimed by someone else or does not exist.",
                    impact="Customer paid for a lead but did not receive it. They will be angry.",
                    action_required="Manually refund the payment in Stripe or email the customer offering a credit.",
                    severity="CRITICAL",
                    throttle_hours=0.0
                )

        # 2. If this was a subscription, register with seniority timestamp
        if not lead_id:
            sub_tier = metadata.get("tier", "climber_domestic")
            # Sep 8 2026: prefer the full postcode when the customer gave one
            # (metadata.full_postcode) -- register_or_update_subscription's
            # resolve_location geocodes that to an exact pin instead of just
            # the outcode centroid. Falls back to the outcode exactly as
            # before when no full postcode was captured.
            sub_location_input = metadata.get("full_postcode") or outcode or "GB"
            sub_radius = int(metadata.get("radius_miles", 15))
            sub_job_size = metadata.get("job_size", "all")
            reg_ok = database.register_or_update_subscription(
                customer_email=customer_email,
                outcode=sub_location_input,
                tier=sub_tier,
                stripe_sub_id=data.get("subscription", ""),
                radius=sub_radius,
                job_size_preference=sub_job_size
            )
            logger.info(f"[Stripe] Subscription registered for {mask(customer_email)} ({sub_tier} in {sub_location_input} ±{sub_radius}mi, job_size={sub_job_size}): {reg_ok}")
            if reg_ok:
                _mark_stripe_event_fulfilled(event_id)
                # Sep 11 2026, Nick's ask ("we should have an 'i agree to
                # terms and conditions' button"): checkout_post already
                # server-side rejected this checkout entirely if the
                # /checkout/{plan_key} form's checkbox wasn't ticked, so by
                # the time Stripe fires this webhook consent has already
                # happened -- this just stamps when the row it applies to
                # actually landed. COALESCE inside the function means a
                # later plan-change webhook re-running this never
                # overwrites the original acceptance timestamp.
                database.record_subscription_terms_acceptance(customer_email)

        logger.info(f"[Stripe] Payment complete — {mask(customer_email)} — £{amount / 100:.2f}")
        return {"event": "payment_complete", "email": customer_email,
                "session_id": session_id, "amount_pence": amount, "outcode": outcode, "lead_id": lead_id}


    elif event_type == "customer.subscription.created":
        customer_id = data.get("customer")
        plan_id = data.get("items", {}).get("data", [{}])[0].get("plan", {}).get("id", "unknown")
        logger.info(f"[Stripe] New subscription — customer {customer_id} — plan {plan_id}")
        return {"event": "subscription_created", "customer_id": customer_id}

    elif event_type == "customer.subscription.updated":
        # Plan upgrade/downgrade via the Stripe Billing Portal fires this event, not
        # checkout.session.completed — previously nothing handled it at all, so a
        # contractor's tier/quota never changed on a self-service plan switch.
        subscription_id = data.get("id")
        items = data.get("items", {}).get("data", [])
        new_price = (items[0].get("price") or {}) if items else {}
        new_amount = new_price.get("unit_amount")
        import database
        # Best-effort reverse-match of the new Stripe price back to a PLANS tier by
        # amount. A couple of tiers share a price point (e.g. sole_trader/climber_domestic
        # are both £49/mo), so an ambiguous match is flagged for manual review rather
        # than guessed at.
        matches = [k for k, v in PLANS.items() if v["mode"] == "subscription" and v["amount"] == new_amount]
        if len(matches) == 1:
            new_tier = matches[0]
            ok = database.update_subscription_tier_by_stripe_id(subscription_id, new_tier)
            logger.info(f"[Stripe] Subscription {subscription_id} plan change -> {new_tier} (£{(new_amount or 0) / 100:.2f}/mo): updated={ok}")
            return {"event": "subscription_updated", "subscription_id": subscription_id, "new_tier": new_tier}
        else:
            logger.warning(f"[Stripe] Subscription {subscription_id} updated to £{(new_amount or 0) / 100:.2f}/mo — {len(matches)} PLANS tiers match ({matches}), cannot auto-resolve.")
            notifications.send_system_incident_alert(
                category="REVENUE & BILLING",
                title=f"SUBSCRIPTION PLAN CHANGE NEEDS MANUAL REVIEW: {subscription_id}",
                description=f"Subscription {subscription_id} changed to a new price (£{(new_amount or 0) / 100:.2f}/mo) that could not be uniquely matched to a PLANS tier (candidates: {matches}).",
                impact="This contractor's tier/quota was NOT updated automatically and may now not match what they're paying for.",
                action_required="Check the Stripe subscription and update contractor_subscriptions.tier for this customer manually.",
                severity="WARNING",
                throttle_hours=1.0
            )
            return {"event": "subscription_updated", "subscription_id": subscription_id, "new_tier": None}

    elif event_type == "customer.subscription.deleted":
        customer_id = data.get("customer")
        subscription_id = data.get("id")
        logger.info(f"[Stripe] Subscription cancelled - customer {customer_id}")
        import database
        if subscription_id:
            database.unlock_territory_by_subscription(subscription_id)
        return {"event": "subscription_cancelled", "customer_id": customer_id}

    elif event_type == "invoice.payment_failed":
        customer_email = data.get("customer_email", "unknown")
        amount_due = data.get("amount_due", 0) / 100
        logger.warning(f"[Stripe] Payment failed — {mask(customer_email)}")
        notifications.send_system_incident_alert(
            category="REVENUE & BILLING",
            title=f"CUSTOMER PAYMENT FAILED (£{amount_due:.2f} — {mask(customer_email)})",
            description=f"A recurring subscription renewal charge failed for customer {mask(customer_email)}.",
            impact="Customer's card was declined. If uncollected, their territory subscription will lapse.",
            action_required="Log into Stripe Dashboard (Payments > Failed) to check decline reason or contact the contractor directly.",
            metric_details={"Customer": customer_email, "Amount": f"£{amount_due:.2f}"},
            severity="WARNING",
            throttle_hours=12.0
        )
        return {"event": "payment_failed", "email": customer_email}

    logger.info(f"[Stripe] Unhandled event type: {event_type}")
    return {"event": event_type, "status": "unhandled"}

