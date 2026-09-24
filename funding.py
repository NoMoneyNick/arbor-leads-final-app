"""
funding.py -- Keeps "the customer paid" separate from "TreeKey has money it
can safely spend on postage", per the brief's section 3.

Five distinct things, deliberately not collapsed into one:

  1. payment_confirmed      -- a signed Stripe event says this specific
                                payment/subscription charge succeeded.
  2. funds_pending_or_available -- Stripe's own view of whether the money
                                has cleared (not implemented against the
                                live Stripe Balance/Payout API in this
                                pass -- see LIMITATIONS below).
  3. payout_evidence        -- proof the money actually reached the bank
                                (not implemented -- see LIMITATIONS).
  4. mailing_budget_available -- TreeKey's own confirmation that there is
                                money set aside to pay a postal vendor.
                                THIS is what gates a real submission today.
  5. eligible_for_submission -- the computed AND of everything else
                                (approved template + not suppressed +
                                address present + funding gate open).

LIMITATIONS (explicit, not hidden):
This pass does NOT implement live Stripe Balance/Payout reconciliation --
tying one specific order to one specific bank credit is a materially bigger
piece of work (Stripe balance transactions, payout objects, the fact that a
payout bundles many charges, refund/dispute clawback timing) and the brief
itself allows a fallback for exactly this case: "a restricted, audited admin
action to confirm a funded mailing budget." That's what's implemented here --
FundingGate.confirm_budget() -- an admin-only, logged action that sets a
budget ceiling TreeKey staff have separately verified is real (e.g. by
looking at the actual bank balance), NOT an automatic Stripe-derived proof.
Wiring genuine payout reconciliation is listed as outstanding work in
docs/launch_checklist.md, not silently declared done.

DEFAULT BEHAVIOUR: FUNDING_MODE=hold. No submission is ever eligible unless
an admin has explicitly confirmed a budget (via confirm_budget) or the
gate is running in "simulate" mode for local development/tests, or a future
explicitly-enabled working_capital mode is on (FUNDING_MODE=working_capital,
NOT the default, must be turned on deliberately -- see class docstring).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("treekey-funding")

FUNDING_MODES = ("hold", "simulate", "working_capital")


def init_funding_schema(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mailing_budget_confirmations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            amount_pence INT NOT NULL,
            confirmed_by TEXT NOT NULL,
            note TEXT,
            spent_pence INT NOT NULL DEFAULT 0,
            reserved_pence INT NOT NULL DEFAULT 0,  -- 2026-09-18 review, Section 6: pence set
            -- aside for a submission attempt that hasn't yet settled or been released --
            -- see FundingGate.reserve()/release()/settle() below. Real headroom for a NEW
            -- reservation or spend is always (amount_pence - spent_pence - reserved_pence).
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_at TIMESTAMPTZ DEFAULT NOW()
        );
        CREATE INDEX IF NOT EXISTS idx_budget_active ON mailing_budget_confirmations(active) WHERE active = TRUE;

        CREATE TABLE IF NOT EXISTS funding_reservations (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            obligation_id TEXT NOT NULL,
            budget_confirmation_id UUID NOT NULL REFERENCES mailing_budget_confirmations(id),
            amount_pence INT NOT NULL,
            status TEXT NOT NULL DEFAULT 'reserved',  -- reserved | settled | released
            created_at TIMESTAMPTZ DEFAULT NOW(),
            resolved_at TIMESTAMPTZ
        );
        CREATE INDEX IF NOT EXISTS idx_funding_reservations_obligation ON funding_reservations(obligation_id);
        CREATE INDEX IF NOT EXISTS idx_funding_reservations_reserved ON funding_reservations(status) WHERE status = 'reserved';
    """)


@dataclass
class FundingDecision:
    eligible: bool
    mode: str
    reason: str
    available_pence: Optional[int] = None


@dataclass
class ReservationDecision:
    """Result of FundingGate.reserve() -- see that method's docstring."""
    ok: bool
    reason: str
    reserved_pence: int = 0


class FundingGate:
    """One instance per process/request is fine -- it does not cache
    anything that changes mid-process; every check re-reads the DB (or the
    simulated state) so a fresh admin confirmation takes effect immediately."""

    def __init__(self, mode: Optional[str] = None):
        self.mode = (mode or os.getenv("FUNDING_MODE", "hold")).strip().lower()
        if self.mode not in FUNDING_MODES:
            logger.warning(f"[Funding] Unknown FUNDING_MODE={self.mode!r}, falling back to 'hold' (fail safe).")
            self.mode = "hold"
        # Only used when mode == "simulate" (tests/local dev). Never read in
        # "hold" or "working_capital" mode.
        self._simulated_available_pence: Optional[int] = None

    def simulate_budget(self, available_pence: int) -> None:
        """Test/dev only. Raises if called outside simulate mode so a test
        can't accidentally believe it's testing production behaviour."""
        if self.mode != "simulate":
            raise RuntimeError("simulate_budget() only valid when mode == 'simulate'.")
        self._simulated_available_pence = available_pence

    def confirm_budget(self, cur, *, amount_pence: int, confirmed_by: str, note: str) -> str:
        """The restricted, audited admin action. Caller (main.py) must gate
        this behind verify_dashboard_auth or an equivalent admin check --
        this function itself does not check identity, it only records who
        the caller says did it, which is why main.py must never let this be
        reached without real admin auth."""
        if amount_pence <= 0:
            raise ValueError("amount_pence must be positive.")
        cur.execute("""
            INSERT INTO mailing_budget_confirmations (amount_pence, confirmed_by, note)
            VALUES (%s, %s, %s) RETURNING id;
        """, (amount_pence, confirmed_by, note))
        budget_id = str(cur.fetchone()[0])
        logger.info(f"[Funding] Budget confirmed by {confirmed_by}: £{amount_pence/100:.2f} ({note}) -> {budget_id}")
        return budget_id

    def _available_pence_from_db(self, cur) -> int:
        # 2026-09-18 review, Section 6: subtracts reserved_pence too, not
        # just spent_pence -- an obligation with an open reservation has
        # already claimed that headroom, so a SECOND obligation's
        # promote_pending_funding check must not see it as still
        # available (this is the cheap, non-atomic PRELIMINARY filter;
        # reserve() below is what actually enforces this atomically).
        cur.execute("""
            SELECT COALESCE(SUM(amount_pence - spent_pence - reserved_pence), 0)
            FROM mailing_budget_confirmations WHERE active = TRUE;
        """)
        return int(cur.fetchone()[0] or 0)

    def check(self, cur, *, estimated_cost_pence: int) -> FundingDecision:
        if self.mode == "working_capital":
            # Explicitly enabled elsewhere (see class docstring + config
            # example) -- sends proceed on confirmed payment alone, without
            # waiting for a separately confirmed mailing budget. This is
            # NOT the default and nothing in this codebase turns it on by
            # itself.
            return FundingDecision(eligible=True, mode=self.mode,
                                    reason="working_capital mode: funded from existing postage funds by explicit configuration.")

        if self.mode == "simulate":
            available = self._simulated_available_pence
            if available is None:
                return FundingDecision(eligible=False, mode=self.mode,
                                        reason="simulate mode active but simulate_budget() was never called -- fails safe.")
            eligible = available >= estimated_cost_pence
            return FundingDecision(eligible=eligible, mode=self.mode,
                                    reason="simulated budget sufficient" if eligible else "simulated budget insufficient",
                                    available_pence=available)

        # mode == "hold" (default): only an explicit, audited admin
        # confirmation opens the gate.
        available = self._available_pence_from_db(cur)
        eligible = available >= estimated_cost_pence
        return FundingDecision(
            eligible=eligible, mode=self.mode,
            reason=("confirmed mailing budget covers this letter" if eligible
                    else "no confirmed mailing budget (or insufficient) -- held pending admin confirm_budget()"),
            available_pence=available,
        )

    def spend(self, cur, amount_pence: int) -> None:
        """Debits the oldest active confirmed budget(s) FIFO. Called only
        after a real (non-dry-run) provider acceptance -- never for a
        dry-run or a merely 'ready' obligation.

        2026-09-18 review, Section 6: this is a lower-level primitive kept
        for direct/manual use (e.g. an admin reconciling an external spend
        that never went through the obligation pipeline at all). The
        automated pipeline (letter_providers.registry.attempt_send) does
        NOT call this directly -- it calls reserve() before attempting a
        send and settle()/release() to resolve that specific reservation
        afterwards, which is what actually prevents two concurrent
        obligations from racing to spend the same headroom (spend() alone,
        called only after the fact, has no such protection: two workers
        could both pass a pre-send eligibility check against the same
        available balance before either had spent anything)."""
        remaining = amount_pence
        cur.execute("""
            SELECT id, amount_pence, spent_pence FROM mailing_budget_confirmations
            WHERE active = TRUE AND spent_pence < amount_pence ORDER BY created_at ASC FOR UPDATE;
        """)
        rows = cur.fetchall()
        for budget_id, amt, spent in rows:
            if remaining <= 0:
                break
            headroom = amt - spent
            take = min(headroom, remaining)
            cur.execute("UPDATE mailing_budget_confirmations SET spent_pence = spent_pence + %s WHERE id = %s;",
                        (take, budget_id))
            remaining -= take
        if remaining > 0:
            logger.error(f"[Funding] spend() could not fully account for £{amount_pence/100:.2f} -- "
                         f"£{remaining/100:.2f} unaccounted. Budget confirmations may be stale; admin review needed.")

    # -----------------------------------------------------------------
    # 2026-09-18 review, Section 6: "Confirm funding is reserved
    # atomically across concurrent workers, so two letters cannot spend
    # the same remaining budget. Preserve reservations for uncertain
    # submissions until reconciled."
    #
    # Called by letter_providers.registry.attempt_send immediately before
    # the provider loop, ONLY when is_dry_run=False -- deliberately NOT at
    # worker.promote_pending_funding time, because is_dry_run is a
    # run_batch-level choice not known until send time, and reserving real
    # budget against an obligation that will only ever be dry-run-sent
    # would tie up money for nothing. promote_pending_funding's check()
    # (above) stays a cheap, non-atomic PRELIMINARY filter so a batch
    # doesn't even attempt an obviously-unfunded obligation; reserve() is
    # what actually protects the money.
    #
    # Concurrency safety: SELECT ... FOR UPDATE locks every candidate
    # budget row for the rest of this transaction -- the exact technique
    # spend() above already uses. A second reserve()/spend() call (from a
    # different worker, on a different connection/transaction) racing for
    # the SAME budget row blocks on that lock until this transaction
    # commits or rolls back, so it can never read stale headroom and
    # reserve pence that's already spoken for. This depends on both
    # callers actually being inside a real transaction on a real
    # connection (true for every call site in this codebase -- see
    # database.py's discipline of one connection/transaction per sale or
    # per worker batch).
    # -----------------------------------------------------------------

    def reserve(self, cur, obligation_id: str, amount_pence: int) -> ReservationDecision:
        """Reserves amount_pence against confirmed budget(s), FIFO by
        confirmation age, possibly split across more than one budget row
        (recorded as one funding_reservations row per split so the whole
        reservation is traceable back to its sources). All-or-nothing: if
        the full amount can't be covered, NOTHING is written and ok=False
        is returned -- never a partial reservation left dangling.

        Only meaningful in 'hold' mode, the only mode backed by real
        mailing_budget_confirmations rows -- 'working_capital' is
        unconditionally eligible by design (no budget to protect) and
        'simulate' is single-process test/dev state never shared across
        concurrent workers; both return ok=True immediately without
        writing a funding_reservations row, since there is nothing later
        to reconcile.

        Idempotent per obligation: if a 'reserved' row already exists for
        this exact obligation_id (e.g. a worker process crashed between
        reserving and actually calling the provider, then retried the
        same obligation), returns ok=True referencing the EXISTING
        reservation rather than reserving a second time -- this is what
        makes it safe to call more than once for the same obligation, and
        is part of what 'preserve reservations for uncertain submissions
        until reconciled' means in practice: a retried attempt doesn't
        need a fresh reservation, the old one is still good."""
        if self.mode != "hold":
            return ReservationDecision(ok=True, reason=f"{self.mode} mode: no real budget to reserve.",
                                        reserved_pence=amount_pence)
        if amount_pence <= 0:
            return ReservationDecision(ok=True, reason="Zero/negative amount -- nothing to reserve.",
                                        reserved_pence=0)

        cur.execute("""
            SELECT COALESCE(SUM(amount_pence), 0) FROM funding_reservations
            WHERE obligation_id = %s AND status = 'reserved';
        """, (obligation_id,))
        already_reserved = int(cur.fetchone()[0] or 0)
        if already_reserved > 0:
            return ReservationDecision(ok=True, reason="Already reserved for this obligation (idempotent retry).",
                                        reserved_pence=already_reserved)

        cur.execute("""
            SELECT id, amount_pence, spent_pence, reserved_pence FROM mailing_budget_confirmations
            WHERE active = TRUE AND (amount_pence - spent_pence - reserved_pence) > 0
            ORDER BY created_at ASC FOR UPDATE;
        """)
        rows = cur.fetchall()
        remaining = amount_pence
        splits = []
        for budget_id, amt, spent, reserved in rows:
            if remaining <= 0:
                break
            headroom = amt - spent - reserved
            take = min(headroom, remaining)
            if take <= 0:
                continue
            splits.append((budget_id, take))
            remaining -= take

        if remaining > 0:
            # Cannot cover the full amount -- nothing has been written yet
            # (the loop above only accumulated `splits` in Python), so
            # there is nothing to roll back. Fail cleanly.
            return ReservationDecision(
                ok=False,
                reason=f"Insufficient confirmed mailing budget: could only cover "
                       f"£{(amount_pence - remaining)/100:.2f} of £{amount_pence/100:.2f} needed.",
                reserved_pence=0,
            )

        for budget_id, take in splits:
            cur.execute("UPDATE mailing_budget_confirmations SET reserved_pence = reserved_pence + %s WHERE id = %s;",
                         (take, budget_id))
            cur.execute("""
                INSERT INTO funding_reservations (obligation_id, budget_confirmation_id, amount_pence, status)
                VALUES (%s, %s, %s, 'reserved');
            """, (obligation_id, budget_id, take))

        logger.info(f"[Funding] Reserved £{amount_pence/100:.2f} for obligation {obligation_id} "
                    f"across {len(splits)} budget row(s).")
        return ReservationDecision(ok=True, reason="Reserved.", reserved_pence=amount_pence)

    def release(self, cur, obligation_id: str) -> int:
        """Releases every still-'reserved' funding_reservations row for
        this obligation back to its budget row's headroom (reserved_pence
        -= amount) and marks each row 'released'. Call this ONLY when the
        submission attempt definitely did NOT spend the money -- i.e. every
        usable provider confirmed rejection (SendOutcome.final_status ==
        'failed'). Never call this for an 'unknown' outcome: see this
        class's own module-level docstring section above -- an ambiguous
        response might already have been accepted by the provider, and
        releasing its reservation would let that same money be reserved
        and spent again elsewhere while the original letter may still be
        in the post. Returns the total pence released (0 if nothing was
        reserved, including outside 'hold' mode)."""
        if self.mode != "hold":
            return 0
        cur.execute("""
            SELECT id, budget_confirmation_id, amount_pence FROM funding_reservations
            WHERE obligation_id = %s AND status = 'reserved' FOR UPDATE;
        """, (obligation_id,))
        rows = cur.fetchall()
        total = 0
        for reservation_id, budget_id, amount in rows:
            cur.execute("UPDATE mailing_budget_confirmations SET reserved_pence = reserved_pence - %s WHERE id = %s;",
                         (amount, budget_id))
            cur.execute("UPDATE funding_reservations SET status = 'released', resolved_at = NOW() WHERE id = %s;",
                         (reservation_id,))
            total += amount
        if total:
            logger.info(f"[Funding] Released £{total/100:.2f} reserved for obligation {obligation_id} (not spent).")
        return total

    def settle(self, cur, obligation_id: str) -> int:
        """Converts every still-'reserved' funding_reservations row for
        this obligation into real spend: moves the same amount from
        reserved_pence to spent_pence on its budget row, and marks the
        reservation row 'settled'. Call this ONLY after a real provider
        outcome of accepted/dispatched. This is the reservation-aware
        counterpart to spend() above -- an obligation that went through
        reserve() first already has its money set aside, so settle() moves
        it from 'set aside' to 'spent' rather than re-spending fresh
        headroom a second time. Returns the total pence settled (0 if
        nothing was reserved, including outside 'hold' mode)."""
        if self.mode != "hold":
            return 0
        cur.execute("""
            SELECT id, budget_confirmation_id, amount_pence FROM funding_reservations
            WHERE obligation_id = %s AND status = 'reserved' FOR UPDATE;
        """, (obligation_id,))
        rows = cur.fetchall()
        total = 0
        for reservation_id, budget_id, amount in rows:
            cur.execute("""
                UPDATE mailing_budget_confirmations
                SET reserved_pence = reserved_pence - %s, spent_pence = spent_pence + %s
                WHERE id = %s;
            """, (amount, amount, budget_id))
            cur.execute("UPDATE funding_reservations SET status = 'settled', resolved_at = NOW() WHERE id = %s;",
                         (reservation_id,))
            total += amount
        if total:
            logger.info(f"[Funding] Settled £{total/100:.2f} reserved for obligation {obligation_id} as spent.")
        return total
