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
        "description": "For qualified arborists (TechArb/MICFor). Developer condition 7 discharges, BS5837 impact assessments, and direct developer company contacts.",
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
        "description": "100% Unrestricted Access to ALL categories across 30 miles + Zero-Minute Instant WhatsApp Alerts + RAMS Legal Pack + Automated Direct Mailouts.",
        "amount": 17900,  # £179/month
        "mode": "subscription",
        "badge": "VIP All-Access",
        "real_world_roi": "Complete business operating system. First-mover WhatsApp dispatch before competitors even know the job exists."
    },
    # Single Lead Pay-As-You-Go Purchases (Single-Sale Inventory Burn)
    "single_lead_small": {
        "name": "Single Lead Unlock (Domestic Maintenance)",
        "description": "100% Exclusive unshared planning lead. Once purchased, this lead is permanently deleted from all systems and never sold again.",
        "amount": 1900,   # £19 one-off
        "mode": "payment",
        "badge": "Single Purchase",
        "real_world_roi": "Instant unlocked property address, homeowner name, and Street View brief."
    },
    "single_lead_medium": {
        "name": "Single Lead Unlock (Standard Felling / Tree Removal)",
        "description": "100% Exclusive unshared felling lead. Permanently burned from inventory upon purchase.",
        "amount": 2900,   # £29 one-off
        "mode": "payment",
        "badge": "Single Purchase",
        "real_world_roi": "Instant unlocked property address, homeowner name, and Street View brief."
    },
    "single_lead_large": {
        "name": "Single Lead Unlock (Commercial / Site Clearance / TPO)",
        "description": "100% Exclusive high-value commercial or developer planning lead. Burned from inventory immediately upon purchase.",
        "amount": 4900,   # £49 one-off
        "mode": "payment",
        "badge": "Single Purchase",
        "real_world_roi": "Instant unlocked property address, developer contact, and planning specs."
    }
}


def create_checkout_session(plan_key: str, outcode: str = None, lead_id: str = None) -> Optional[str]:
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
        price_data = {
            "currency": "gbp",
            "product_data": {"name": plan["name"], "description": plan["description"]},
            "unit_amount": plan["amount"],
        }
        if plan["mode"] == "subscription":
            price_data["recurring"] = {"interval": "month"}

        session_params = {
            "line_items": [{"price_data": price_data, "quantity": 1}],
            "mode": plan["mode"],
            "success_url": f"{PUBLIC_APP_URL}/payment/success?session_id={{CHECKOUT_SESSION_ID}}",
            "cancel_url": f"{PUBLIC_APP_URL}/pricing",
            "allow_promotion_codes": True,
            "metadata": {}
        }
        
        if outcode:
            session_params["client_reference_id"] = outcode
            session_params["metadata"]["outcode"] = outcode
            
        if lead_id:
            session_params["metadata"]["lead_id"] = lead_id

        session = stripe.checkout.Session.create(**session_params)
        logger.info(f"[Stripe] Checkout session created for plan '{plan_key}' (lead_id={lead_id}): {session.id}")
        return session.url

    except stripe.error.AuthenticationError:
        logger.error("[Stripe] Invalid API key.")
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
        notifications.send_system_incident_alert(
            category="SECURITY & PAYMENTS",
            title="STRIPE WEBHOOK SIGNATURE MISMATCH / INVALID SIGNATURE",
            description="WARNING: An incoming webhook payload failed cryptographic signature verification against STRIPE_WEBHOOK_SECRET.",
            impact="The webhook was rejected to protect against spoofed transactions. If this was a legitimate Stripe event, webhook fulfillment failed.",
            action_required="1. Verify STRIPE_WEBHOOK_SECRET in Render matches the Signing Secret in Stripe Dashboard. 2. Check if a new webhook endpoint was created in Stripe.",
            severity="WARNING",
            throttle_hours=2.0
        )
        return {"error": "Invalid signature"}
    except Exception as e:
        logger.error(f"[Stripe Webhook] Parse error: {e}")
        return {"error": str(e)}

    event_type = event["type"]
    data = event["data"]["object"]
    
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
            burned = database.burn_lead_inventory(lead_id, customer_email)
            logger.info(f"[Stripe] Lead {lead_id} burned from inventory for {mask(customer_email)}: {burned}")

        # 2. If this was a subscription with outcode
        if outcode and not lead_id:
            claimed = database.claim_territory_atomically(outcode, customer_email, stripe_sub_id=data.get("subscription", ""))
            logger.info(f"[Stripe] Territory {outcode} claimed by {mask(customer_email)}: {claimed}")
            if not claimed:
                import notifications
                notifications.send_system_incident_alert(
                    category="PAYMENTS & REVENUE",
                    title="CRITICAL PAYMENT RACE CONDITION",
                    description=f"Customer {mask(customer_email)} paid for territory {outcode}, but the DB lock failed (likely claimed seconds prior).",
                    impact="Customer was charged but their territory is NOT active.",
                    action_required="Log into Stripe immediately. Refund the payment or contact the customer to select a new territory.",
                    severity="CRITICAL"
                )
        logger.info(f"[Stripe] Payment complete — {mask(customer_email)} — £{amount / 100:.2f}")
        return {"event": "payment_complete", "email": customer_email,
                "session_id": session_id, "amount_pence": amount, "outcode": outcode, "lead_id": lead_id}


    elif event_type == "customer.subscription.created":
        customer_id = data.get("customer")
        plan_id = data.get("items", {}).get("data", [{}])[0].get("plan", {}).get("id", "unknown")
        logger.info(f"[Stripe] New subscription — customer {customer_id} — plan {plan_id}")
        return {"event": "subscription_created", "customer_id": customer_id}

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

