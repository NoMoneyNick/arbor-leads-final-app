"""
letter_providers/registry.py -- Orders providers, health-tracks them, and is
the ONLY place that decides whether to fall back to the next provider.

Fallback rule (section 6, load-bearing -- read before changing):
  Falls back to the next provider ONLY when the current attempt's outcome is
  OUTCOME_REJECTED (a confirmed non-acceptance) or when a prior 'unknown'
  attempt has since been reconciled via check_status() into a confirmed
  rejection. It NEVER falls back on OUTCOME_UNKNOWN directly -- an unknown
  outcome stops the obligation at status='unknown' for human reconciliation,
  full stop. This is the single most important invariant in this file:
  timeouts/crashes/ambiguous responses must never cause a second provider to
  be tried for the same obligation, because the first attempt may already
  have been accepted.
"""
from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass, field
from typing import Optional

import fulfilment
import suppression
from letter_providers.base import (
    LetterProviderAdapter, LetterRequest, ProviderResult, ProviderCapabilities, fingerprint_content,
    OUTCOME_ACCEPTED, OUTCOME_DISPATCHED, OUTCOME_REJECTED, OUTCOME_UNKNOWN,
)

logger = logging.getLogger("treekey-provider-registry")

# How many consecutive rejections/unknowns before a provider is temporarily
# suspended from automatic selection (still selectable by an explicit admin
# override, not implemented in this pass -- see launch checklist).
DEFAULT_UNHEALTHY_THRESHOLD = 3


@dataclass
class ProviderSlot:
    adapter: LetterProviderAdapter
    enabled: bool = True
    cost_ceiling_pence: Optional[int] = None
    consecutive_failures: int = 0
    suspended: bool = False
    role: str = ""  # "primary" / "backup_1" / "backup_2" when built via
    # build_registry_from_env() below -- optional, defaults to "" so
    # existing direct ProviderSlot(...) construction (tests, any future
    # manual wiring) is unaffected.

    def record_outcome(self, outcome: str) -> None:
        if outcome in (OUTCOME_REJECTED, OUTCOME_UNKNOWN):
            self.consecutive_failures += 1
            if self.consecutive_failures >= DEFAULT_UNHEALTHY_THRESHOLD:
                self.suspended = True
                logger.warning(f"[Registry] Provider {self.adapter.name!r} suspended after "
                                f"{self.consecutive_failures} consecutive non-accept outcomes.")
        else:
            self.consecutive_failures = 0
            self.suspended = False

    def usable(self, estimated_cost_pence: Optional[int]) -> bool:
        if not self.enabled or self.suspended or not self.adapter.is_configured():
            return False
        if self.cost_ceiling_pence is not None and estimated_cost_pence is not None:
            if estimated_cost_pence > self.cost_ceiling_pence:
                return False
        return True


class ProviderRegistry:
    def __init__(self, slots: list[ProviderSlot]):
        self.slots = slots  # order = priority; first usable slot wins

    def usable_slots(self, estimated_cost_pence: Optional[int] = None):
        return [s for s in self.slots if s.usable(estimated_cost_pence)]

    def describe(self) -> list[dict]:
        """Admin-visibility helper (2026-09-18 review, Section 1): a plain,
        loggable/renderable snapshot of every slot -- including a disabled
        one, which is exactly the point (an operator/admin page reading
        this can show 'backup_2: unconfigured' rather than that slot simply
        not appearing anywhere)."""
        return [
            {
                "role": slot.role or f"slot_{i}",
                "provider_kind": getattr(slot.adapter, "name", "unknown"),
                "enabled": slot.enabled,
                "configured": slot.adapter.is_configured(),
                "suspended": slot.suspended,
                "consecutive_failures": slot.consecutive_failures,
            }
            for i, slot in enumerate(self.slots)
        ]


# ---------------------------------------------------------------------------
# Section 1 (2026-09-18 review): "Confirm there are exactly three
# configurable, provider-independent slots: primary, backup 1 and backup 2.
# All companies remain unchosen." -- the factory below is what makes that a
# structural, testable fact rather than an operator convention: every
# ProviderRegistry built this way has EXACTLY three ProviderSlot entries,
# always, in the fixed priority order primary -> backup_1 -> backup_2, and a
# slot with no (or an unrecognised) LETTER_PROVIDER_* value is a REAL slot
# that is permanently disabled (enabled=False AND an adapter whose
# is_configured() is hard-coded False) rather than an absent one. Which
# concrete provider (Stannp, a future vendor, or the fake test adapter)
# occupies a slot is entirely config-driven and independent of the slot's
# role -- nothing here hard-codes "primary = Stannp" or similar.
# ---------------------------------------------------------------------------

SLOT_ROLES = ("primary", "backup_1", "backup_2")

LETTER_PROVIDER_ENV_VARS = {
    "primary": "LETTER_PROVIDER_PRIMARY",
    "backup_1": "LETTER_PROVIDER_BACKUP_1",
    "backup_2": "LETTER_PROVIDER_BACKUP_2",
}


def _provider_kinds() -> dict:
    """Built lazily (not at module import time) so importing this module
    never requires letter_providers.stannp_provider et al. to import
    cleanly first -- keeps this module's own import graph shallow and
    matches how the rest of this package avoids import-order surprises."""
    from letter_providers.fake_provider import FakeLetterProvider
    from letter_providers.stannp_provider import StannpProvider
    from letter_providers.intelliprint_provider import IntelliprintProvider
    from letter_providers.postworks_provider import PostworksProvider
    return {
        "fake_test": FakeLetterProvider,
        "stannp": StannpProvider,
        "intelliprint": IntelliprintProvider,
        "postworks": PostworksProvider,
    }


class _UnconfiguredSlotProvider(LetterProviderAdapter):
    """Occupies a slot with no recognised LETTER_PROVIDER_* value. Always
    reports not-configured -- belt-and-braces alongside the slot's own
    enabled=False, so even a future bug that ignored `ProviderSlot.enabled`
    still could not select this slot (usable() checks both). send() raises
    loudly rather than silently doing nothing if it's ever reached, since
    reaching it at all would itself be the bug worth surfacing loudly."""

    def __init__(self, role: str):
        self.role = role
        self.name = f"unconfigured_{role}"
        self.capabilities = ProviderCapabilities()

    def is_configured(self) -> bool:
        return False

    def send(self, request: LetterRequest) -> ProviderResult:
        raise RuntimeError(
            f"The {self.role!r} provider slot has no provider configured -- this call should be "
            f"unreachable, since ProviderSlot.usable() must never select a disabled/unconfigured slot."
        )


def build_registry_from_env() -> ProviderRegistry:
    """Reads LETTER_PROVIDER_PRIMARY / LETTER_PROVIDER_BACKUP_1 /
    LETTER_PROVIDER_BACKUP_2 fresh from the environment every call (same
    convention as fulfilment.active_pipeline()) and returns a
    ProviderRegistry with exactly three slots in that fixed priority order.

    A slot whose env var is unset, blank, or set to a value not in
    _provider_kinds() is built as a disabled, permanently-unconfigured slot
    -- never guesses a provider, never silently drops the slot from the
    list. This is deliberately the ONLY supported way to select which real
    provider (if any) fills a slot; nothing in this codebase pre-selects a
    vendor for any slot."""
    kinds = _provider_kinds()
    slots = []
    for role in SLOT_ROLES:
        env_var = LETTER_PROVIDER_ENV_VARS[role]
        kind = os.getenv(env_var, "").strip().lower()
        if not kind:
            slots.append(ProviderSlot(adapter=_UnconfiguredSlotProvider(role), enabled=False, role=role))
            continue
        provider_cls = kinds.get(kind)
        if provider_cls is None:
            logger.warning(f"[Registry] {env_var}={kind!r} is not a recognised provider kind "
                            f"({sorted(kinds)}) -- leaving the {role!r} slot disabled rather than guessing.")
            slots.append(ProviderSlot(adapter=_UnconfiguredSlotProvider(role), enabled=False, role=role))
            continue
        slots.append(ProviderSlot(adapter=provider_cls(), enabled=True, role=role))
    return ProviderRegistry(slots)


@dataclass
class SendOutcome:
    obligation_id: str
    final_status: str
    provider_name: Optional[str] = None
    attempts_made: int = 0
    note: str = ""


def attempt_send(cur, registry: ProviderRegistry, obligation_id: str, *, worker_id: str,
                  content_html: str, address_lines: dict, applicant_name: Optional[str],
                  lead_reference: str, idempotency_key: str, is_dry_run: bool,
                  estimated_cost_pence: Optional[int] = None, gate=None) -> SendOutcome:
    """The one function that actually tries to send a letter. Call sequence:
      1. Re-check suppression (checkpoint 3 -- see suppression.py's docstring).
      2. Atomically claim the obligation (skip cleanly if already claimed).
      3. Compute + record the content fingerprint BEFORE calling any
         provider, so a retry can never silently submit different content.
      4. (2026-09-18 review, Section 6 -- real sends only) Reserve funding
         atomically via `gate.reserve()` BEFORE calling any provider.
      5. Walk usable providers in order; stop at the first ACCEPTED/
         DISPATCHED result, or at the first UNKNOWN result (never continue
         past an unknown -- see module docstring), or after all usable
         providers have confirmed REJECTED.
      6. Resolve the reservation according to the FINAL outcome: settle on
         accepted/dispatched, release on failed (every usable provider
         confirmed rejection), leave untouched on unknown -- see step 4's
         gate.reserve() docstring and funding.FundingGate.release()'s own
         docstring for why 'unknown' must never auto-release.

    `gate`: a funding.FundingGate instance, or None. Reservation
    (steps 4/6) is entirely skipped -- with a loud warning, not silently --
    when gate is None or estimated_cost_pence is None; this keeps every
    pre-existing caller of this function (including this file's own
    fallback-logic test suite, which is about the OUTCOME_UNKNOWN/rejection
    invariant above, not about funding) working unchanged. The real
    production entry point, worker.run_batch, always constructs a real
    FundingGate() (defaulting to FUNDING_MODE=hold, fail-safe) when its
    caller doesn't supply one -- see that function's own docstring. Calling
    this function directly with is_dry_run=False and no gate bypasses
    funding reservation entirely; do not do that in production code.
    """
    match = suppression.is_suppressed(cur, address=address_lines.get("line1", ""), applicant_name=applicant_name)
    if match.suppressed:
        fulfilment.mark_suppressed(cur, obligation_id, match.reason or "suppressed")
        return SendOutcome(obligation_id=obligation_id, final_status="suppressed",
                            note=f"Blocked at pre-submission suppression check: {match.reason}")

    if not fulfilment.claim_for_submission(cur, obligation_id, worker_id):
        return SendOutcome(obligation_id=obligation_id, final_status="skipped",
                            note="Not claimable (already claimed by another worker, or not 'ready').")

    fingerprint = fingerprint_content(content_html)

    if is_dry_run:
        fulfilment.mark_provider_result(cur, obligation_id, outcome="dry_run", is_dry_run=True,
                                         provider_name="dry_run", content_fingerprint=fingerprint)
        return SendOutcome(obligation_id=obligation_id, final_status="dry_run",
                            provider_name="dry_run", attempts_made=0,
                            note="Dry-run: no provider called, no real timestamp set.")

    # 2026-09-18 review, Section 6: reserve BEFORE calling any provider --
    # this is the atomic step that actually protects the money (see
    # FundingGate.reserve()'s own docstring on the concurrency guarantee).
    # An obligation this cannot be reserved for is stopped here, having
    # never claimed a provider slot at all -- 'failed', mirroring how "no
    # usable provider accepted this letter" is already reported below, so
    # an admin sees one consistent status/recovery path rather than a new
    # bespoke one for this specific cause.
    if gate is None or estimated_cost_pence is None:
        logger.warning(f"[Registry] Obligation {obligation_id}: no funding gate/estimated_cost_pence supplied to "
                        f"attempt_send -- proceeding WITHOUT an atomic funding reservation. Do not call this "
                        f"function this way for a real (non-dry-run) send in production; worker.run_batch always "
                        f"supplies both.")
    else:
        reservation = gate.reserve(cur, obligation_id, estimated_cost_pence)
        if not reservation.ok:
            fulfilment.mark_provider_result(cur, obligation_id, outcome="failed", is_dry_run=False,
                                             provider_name=None, error=f"Funding reservation failed: {reservation.reason}",
                                             content_fingerprint=fingerprint)
            return SendOutcome(obligation_id=obligation_id, final_status="failed", attempts_made=0,
                                note=f"Could not reserve funding -- {reservation.reason}")

    request = LetterRequest(
        idempotency_key=idempotency_key, lead_reference=lead_reference, address_lines=address_lines,
        applicant_name=applicant_name, content_html=content_html, content_fingerprint=fingerprint,
    )

    attempts = 0
    for slot in registry.usable_slots(estimated_cost_pence):
        attempts += 1
        try:
            result = slot.adapter.send(request)
        except Exception as exc:
            # A raising adapter (e.g. the unimplemented Intelliprint/Postworks
            # stubs, or a real crash) is treated as UNKNOWN, never as
            # rejected -- we do not know what happened on the provider's side.
            # The funding reservation is deliberately left untouched (see
            # this function's own docstring, step 6) -- an ambiguous crash
            # may still mean the letter was accepted before the crash.
            logger.error(f"[Registry] Provider {slot.adapter.name!r} raised during send(): {exc}")
            slot.record_outcome(OUTCOME_UNKNOWN)
            fulfilment.mark_provider_result(cur, obligation_id, outcome="unknown", is_dry_run=False,
                                             provider_name=slot.adapter.name, error=str(exc),
                                             content_fingerprint=fingerprint)
            return SendOutcome(obligation_id=obligation_id, final_status="unknown",
                                provider_name=slot.adapter.name, attempts_made=attempts,
                                note="Provider raised an exception -- ambiguous, held for reconciliation, no fallback attempted.")

        slot.record_outcome(result.outcome)

        if result.outcome in (OUTCOME_ACCEPTED, OUTCOME_DISPATCHED):
            outcome_key = "accepted" if result.outcome == OUTCOME_ACCEPTED else "dispatched"
            fulfilment.mark_provider_result(cur, obligation_id, outcome=outcome_key, is_dry_run=False,
                                             provider_name=result.provider_name,
                                             provider_reference=result.provider_reference,
                                             content_fingerprint=fingerprint)
            if gate is not None and estimated_cost_pence is not None:
                gate.settle(cur, obligation_id)
            return SendOutcome(obligation_id=obligation_id, final_status=outcome_key,
                                provider_name=result.provider_name, attempts_made=attempts,
                                note=result.message)

        if result.outcome == OUTCOME_UNKNOWN:
            # Reservation left untouched -- see this function's own
            # docstring, step 6, and FundingGate.release()'s docstring on
            # why 'unknown' must never auto-release.
            fulfilment.mark_provider_result(cur, obligation_id, outcome="unknown", is_dry_run=False,
                                             provider_name=result.provider_name, error=result.message,
                                             content_fingerprint=fingerprint)
            return SendOutcome(obligation_id=obligation_id, final_status="unknown",
                                provider_name=result.provider_name, attempts_made=attempts,
                                note="Ambiguous provider response -- held for reconciliation, no automatic fallback.")

        # OUTCOME_REJECTED: confirmed non-acceptance -- the ONLY case that
        # is allowed to continue the loop and try the next provider.
        logger.info(f"[Registry] Provider {slot.adapter.name!r} confirmed rejection for {lead_reference!r}: "
                     f"{result.message} -- trying next provider if any.")
        continue

    # Every usable provider confirmed rejection (or there were none usable)
    # -- the money was definitely never spent, so release the reservation
    # back to the pool for other obligations to use.
    fulfilment.mark_provider_result(cur, obligation_id, outcome="failed", is_dry_run=False,
                                     provider_name=(registry.slots[0].adapter.name if registry.slots else None),
                                     error="All usable providers confirmed rejection, or none were usable/configured.",
                                     content_fingerprint=fingerprint)
    if gate is not None and estimated_cost_pence is not None:
        gate.release(cur, obligation_id)
    return SendOutcome(obligation_id=obligation_id, final_status="failed", attempts_made=attempts,
                        note="No usable provider accepted this letter.")
