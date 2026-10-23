"""
refund_sync.py -- keeps TreeKey's payment record in step with Stripe's refund state.

WHAT IT DOES
  On a Stripe refund event it asks Stripe for the charge's CURRENT total refunded (never trusts the event
  body for amounts or ordering), then records that absolute figure on the payment row:
    - FULL refund    -> payments.status = 'refunded', refunded_pence = charge amount.
                        (The existing meaning of 'refunded' applies: the £4.99 first offer is released and the
                        lead leaves the buyer's My Leads. Nothing else changes -- see below.)
    - PARTIAL refund -> status stays 'paid', refunded_pence = the amount so far. First offer stays used.
    - nothing refunded (e.g. a refund later failed/was cancelled) -> status back to 'paid', refunded_pence = 0.
  Idempotent: the stored value is the absolute amount Stripe reports, written only if it differs, so a
  repeated or out-of-order delivery changes nothing. Concurrent deliveries are serialised by a row lock and
  each one reads Stripe's state AFTER taking that lock.

WHAT IT DELIBERATELY DOES NOT DO
  It never cancels a letter, never releases an allocation, never touches funding/budget reservations and never
  contacts the provider. No refund policy has been agreed, so those are decisions for Nick; the alert it
  raises states the order's fulfilment stage (read-only) so the decision can be made with the facts.
"""
import logging

logger = logging.getLogger("treekey-refund-sync")

REFUND_EVENT_TYPES = (
    "charge.refunded",
    "charge.refund.updated",
    "refund.created",
    "refund.updated",
    "refund.failed",
)

# Payment statuses a refund may be applied to. 'pending'/'failed'/anything else was never a completed charge here.
_APPLICABLE_STATUSES = ("paid", "refunded", "refund_failed")

# Fulfilment stages (read-only classification of the linked letter order).
_NOT_SUBMITTED = ("blocked_missing_data", "pending_approval", "pending_funding", "ready")
_IN_FLIGHT = ("submitting", "unknown")
_SUBMITTED = ("provider_accepted", "dispatched", "delivered")
_CLOSED = ("failed", "cancelled", "suppressed", "dry_run")


def classify(charge_amount_pence, refunded_pence) -> str:
    """'none' | 'partial' | 'full'. Raises ValueError for figures that cannot be right."""
    for v in (charge_amount_pence, refunded_pence):
        if isinstance(v, bool) or not isinstance(v, int):
            raise ValueError(f"amount must be a whole number of pence, got {v!r}")
    if charge_amount_pence <= 0:
        raise ValueError(f"charge amount must be positive, got {charge_amount_pence}")
    if refunded_pence < 0 or refunded_pence > charge_amount_pence:
        raise ValueError(f"refunded {refunded_pence}p is outside 0..{charge_amount_pence}p")
    if refunded_pence == 0:
        return "none"
    return "full" if refunded_pence == charge_amount_pence else "partial"


def fulfilment_stage(order_status) -> str:
    if order_status is None:
        return "no_order"
    if order_status in _NOT_SUBMITTED:
        return "not_submitted"
    if order_status in _IN_FLIGHT:
        return "in_flight"
    if order_status in _SUBMITTED:
        return "submitted"
    if order_status in _CLOSED:
        return "closed"
    return "unrecognised"


def read_fulfilment_state(cur, stripe_session_id: str) -> dict:
    """Read-only: the allocation and letter order linked to this payment."""
    cur.execute("""
        SELECT la.id::text, la.lead_reference, lo.id::text, lo.status, lo.is_dry_run
        FROM lead_allocations la LEFT JOIN letter_obligations lo ON lo.allocation_id = la.id
        WHERE la.source_payment_ref = %s
        ORDER BY la.created_at ASC;
    """, (stripe_session_id,))
    rows = cur.fetchall()
    if not rows:
        return {"allocation": False, "stage": "no_order", "orders": 0, "lead_reference": None, "order_status": None}
    stages = [fulfilment_stage(r[3]) for r in rows]
    # Report the most advanced stage (submitted > in_flight > not_submitted > closed) so a mixed set is never understated.
    for s in ("submitted", "in_flight", "not_submitted", "unrecognised", "closed", "no_order"):
        if s in stages:
            stage = s
            break
    first = rows[0]
    return {"allocation": True, "stage": stage, "orders": len([r for r in rows if r[2]]),
            "lead_reference": first[1], "order_status": first[3]}


def apply_refund_state(cur, stripe_session_id: str, fetch_state) -> dict:
    """Locks the payment row, then calls fetch_state() -> (charge_amount_pence, refunded_pence) so Stripe is read
    AFTER the lock, then writes the absolute state if (and only if) it differs. The caller commits.
    Returns {found, applicable, changed, kind, previous_status, new_status, previous_refunded_pence, refunded_pence}."""
    cur.execute("""
        SELECT id, status, COALESCE(refunded_pence, 0) FROM payments
        WHERE stripe_session_id = %s FOR UPDATE;
    """, (stripe_session_id,))
    row = cur.fetchone()
    if not row:
        return {"found": False, "applicable": False, "changed": False}
    pay_id, status, prev_refunded = row[0], row[1], int(row[2] or 0)
    if status not in _APPLICABLE_STATUSES:
        return {"found": True, "applicable": False, "changed": False, "previous_status": status}

    charge_amount, refunded = fetch_state()
    kind = classify(charge_amount, refunded)
    if kind == "full":
        new_status = "refunded"
    elif status == "refund_failed":
        new_status = "refund_failed"   # TreeKey's own failed auto-refund stays flagged until Stripe shows it fully refunded
    else:
        new_status = "paid"

    cur.execute("""
        UPDATE payments
        SET status = %s, refunded_pence = %s, refund_synced_at = NOW()
        WHERE id = %s AND (status IS DISTINCT FROM %s OR COALESCE(refunded_pence, 0) IS DISTINCT FROM %s);
    """, (new_status, refunded, pay_id, new_status, refunded))
    changed = cur.rowcount == 1
    return {"found": True, "applicable": True, "changed": changed, "kind": kind, "previous_status": status,
            "new_status": new_status, "previous_refunded_pence": prev_refunded, "refunded_pence": refunded,
            "charge_amount_pence": charge_amount}


def _plain(obj):
    return obj.to_dict() if hasattr(obj, "to_dict") else obj


def _charge_id(data: dict):
    if data.get("object") == "charge" or str(data.get("id", "")).startswith("ch_") or str(data.get("id", "")).startswith("py_"):
        return data.get("id")
    return data.get("charge")


def _money(v):
    return f"£{v / 100:.2f}"


def _alert(result: dict, fulfilment: dict, session_id: str, event_type: str) -> None:
    import notifications
    kind, stage = result["kind"], fulfilment["stage"]
    what = {"full": "FULL REFUND", "partial": "PARTIAL REFUND", "none": "REFUND REVERSED"}[kind]
    advice = {
        "no_order": "No letter order is linked to this payment.",
        "not_submitted": "The letter has NOT been submitted to the provider yet. Decide whether to cancel it (not done automatically).",
        "in_flight": "The letter is mid-submission or awaiting reconciliation. Resolve it before deciding anything.",
        "submitted": "The letter has already been submitted to the provider and cannot be recalled by TreeKey.",
        "closed": "The letter order is already closed (failed, cancelled, suppressed or dry run).",
        "unrecognised": "The letter order is in a state this check does not recognise. Look at it manually.",
    }[stage]
    urgent = kind != "none" and stage in ("in_flight", "submitted") and kind == "full"
    try:
        notifications.send_system_incident_alert(
            category="REVENUE & BILLING",
            title=f"{what} SYNCED: {_money(result['refunded_pence'])} of {_money(result['charge_amount_pence'])} ({stage.replace('_', ' ')})",
            description=(f"Stripe reports {_money(result['refunded_pence'])} refunded of {_money(result['charge_amount_pence'])} "
                         f"for payment {session_id} (event {event_type}). Payment status {result['previous_status']} -> "
                         f"{result['new_status']}."),
            impact=(f"{advice} No letter was cancelled, no allocation was released and no budget was changed. "
                    + ("The £4.99 first offer is released for this account. " if result["new_status"] == "refunded" else "")),
            action_required=("Decide under your refund policy whether the letter order and lead allocation need action. "
                             "Nothing was changed automatically."),
            metric_details={"Payment": session_id, "Lead": fulfilment.get("lead_reference") or "-",
                            "Letter order status": fulfilment.get("order_status") or "-"},
            severity="CRITICAL" if urgent else "WARNING",
            throttle_hours=0.0,
        )
    except Exception as exc:  # the alert is best-effort; the record is already correct
        logger.error(f"[RefundSync] Could not raise the alert for {session_id}: {exc}")


def handle_refund_event(event_type: str, data: dict, event_id=None) -> dict:
    """Entry point from the (signature-verified) webhook. Transient failures return {"error", "retry": True}
    so Stripe redelivers; events that are not about a TreeKey checkout payment are acknowledged and ignored."""
    import stripe
    import database

    charge_id = _charge_id(data)
    if not charge_id:
        return {"event": "refund_ignored", "reason": "no_charge_id"}

    def fetch_charge():
        c = _plain(stripe.Charge.retrieve(charge_id))
        return c

    try:
        payment_intent = data.get("payment_intent") if data.get("object") == "charge" or data.get("object") == "refund" else None
        if not payment_intent:
            payment_intent = fetch_charge().get("payment_intent")
        if not payment_intent:
            return {"event": "refund_ignored", "reason": "no_payment_intent"}
        sessions = _plain(stripe.checkout.Session.list(payment_intent=payment_intent, limit=1))
        found = (sessions or {}).get("data") or []
        if not found:
            return {"event": "refund_ignored", "reason": "no_checkout_session"}
        session_id = found[0].get("id")
    except Exception as exc:
        logger.error(f"[RefundSync] Stripe lookup failed for {charge_id}: {exc}")
        return {"error": "refund_lookup_failed", "retry": True}

    def fetch_state():
        c = fetch_charge()
        if (c.get("currency") or "gbp").lower() != "gbp":
            raise ValueError(f"unexpected currency {c.get('currency')!r}")
        return int(c["amount"]), int(c.get("amount_refunded") or 0)

    conn = database.get_db_conn()
    cur = conn.cursor()
    try:
        result = apply_refund_state(cur, session_id, fetch_state)
        fulfilment = read_fulfilment_state(cur, session_id) if result.get("changed") else None
        conn.commit()
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.error(f"[RefundSync] Could not sync refund for {session_id}: {exc}")
        return {"error": "refund_sync_failed", "retry": True}
    finally:
        cur.close()
        conn.close()

    if not result["found"]:
        return {"event": "refund_ignored", "reason": "no_payment_record"}
    if not result["applicable"]:
        return {"event": "refund_ignored", "reason": f"payment_status_{result.get('previous_status')}"}
    if not result["changed"]:
        return {"event": "refund_already_synced", "kind": result["kind"]}

    logger.info(f"[RefundSync] {session_id}: {result['previous_status']} -> {result['new_status']}, "
                f"refunded {result['refunded_pence']}p of {result['charge_amount_pence']}p ({result['kind']}).")
    _alert(result, fulfilment, session_id, event_type)
    return {"event": "refund_synced", "kind": result["kind"], "status": result["new_status"]}
