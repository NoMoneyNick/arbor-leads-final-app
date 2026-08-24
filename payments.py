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

# ── Pricing Plans ─────────────────────────────────────────────────────────────
# All amounts in pence (GBP)
PLANS = {
    "starter_monthly": {
        "name": "Starter",
        "description": "10 leads per month in one city. Email alert the moment a new application drops. No setup required.",
        "amount": 1900,   # £19/month
        "mode": "subscription",
        "badge": "🌱 Try It Out",
    },
    "credits_10": {
        "name": "10 Lead Credits",
        "description": "Buy 10 exclusive planning application leads. Never shared. Use them at your own pace.",
        "amount": 8000,   # £80 one-off
        "mode": "payment",
        "badge": "🟡 Pay As You Go",
    },
    "city_monthly": {
        "name": "City Pro",
        "description": "Unlimited exclusive leads for one city. Graded by job size. Cancel anytime.",
        "amount": 4900,   # £49/month
        "mode": "subscription",
        "badge": "📍 Most Popular",
    },
    "national_monthly": {
        "name": "National",
        "description": "All cities. Every new city added automatically. First access before lower tiers.",
        "amount": 8900,   # £89/month
        "mode": "subscription",
        "badge": "🇬🇧 Best Value",
    },
}


def create_checkout_session(plan_key: str) -> Optional[str]:

    """
    Creates a Stripe Checkout session for the given plan.
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
        }

        session = stripe.checkout.Session.create(**session_params)
        logger.info(f"[Stripe] Checkout session created for plan '{plan_key}': {session.id}")
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

    if event_type == "checkout.session.completed":
        session_id = data.get("id")
        customer_email = data.get("customer_details", {}).get("email", "unknown")
        amount = data.get("amount_total", 0)
        outcode = data.get("client_reference_id") or data.get("metadata", {}).get("outcode")
        if outcode:
            import database
            claimed = database.claim_territory_atomically(outcode, customer_email, stripe_sub_id=data.get("subscription", ""))
            logger.info(f"[Stripe] Territory {outcode} claimed by {customer_email}: {claimed}")
        logger.info(f"[Stripe] Payment complete — {customer_email} — £{amount / 100:.2f}")
        return {"event": "payment_complete", "email": customer_email,
                "session_id": session_id, "amount_pence": amount, "outcode": outcode}


    elif event_type == "customer.subscription.created":
        customer_id = data.get("customer")
        plan_id = data.get("items", {}).get("data", [{}])[0].get("plan", {}).get("id", "unknown")
        logger.info(f"[Stripe] New subscription — customer {customer_id} — plan {plan_id}")
        return {"event": "subscription_created", "customer_id": customer_id}

    elif event_type == "customer.subscription.deleted":
        customer_id = data.get("customer")
        logger.info(f"[Stripe] Subscription cancelled — customer {customer_id}")
        return {"event": "subscription_cancelled", "customer_id": customer_id}

    elif event_type == "invoice.payment_failed":
        customer_email = data.get("customer_email", "unknown")
        amount_due = data.get("amount_due", 0) / 100
        logger.warning(f"[Stripe] Payment failed — {customer_email}")
        notifications.send_system_incident_alert(
            category="REVENUE & BILLING",
            title=f"CUSTOMER PAYMENT FAILED (£{amount_due:.2f} — {customer_email})",
            description=f"A recurring subscription renewal charge failed for customer {customer_email}.",
            impact="Customer's card was declined. If uncollected, their territory subscription will lapse.",
            action_required="Log into Stripe Dashboard (Payments > Failed) to check decline reason or contact the contractor directly.",
            metric_details={"Customer": customer_email, "Amount": f"£{amount_due:.2f}"},
            severity="WARNING",
            throttle_hours=12.0
        )
        return {"event": "payment_failed", "email": customer_email}

    logger.info(f"[Stripe] Unhandled event type: {event_type}")
    return {"event": event_type, "status": "unhandled"}

