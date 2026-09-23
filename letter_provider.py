"""
=============================================================================
VECTOR DATA LABS — LETTER PROVIDER ABSTRACTION (Sep 2026)
=============================================================================
Built for Nick's business-model pivot: every unlocked/sold lead must now
trigger a real postal letter to the applicant, no exceptions -- and that
letter is also the delivery vehicle for the UK GDPR Article 14 notice (see
main.py's generate_homeowner_letter for the actual letter content/template).
database.py's _queue_letter_dispatch is what guarantees a letter_dispatches
row exists for every sale; this file is only about HOW a queued row
actually gets put in the post, and is deliberately kept separate so the
"no address sold without a letter queued" guarantee doesn't depend on which
vendor ends up sending it.

Vendor not chosen yet as of this writing. Rather than block the compliance
mechanism (the queue, the enforcement in database.py) on that decision,
this defaults to ConsoleLetterProvider -- it logs exactly what WOULD be
sent and marks the dispatch 'queued_dry_run' instead of 'sent', so:
  - nothing here can accidentally spend real money or attempt a real send
    before a vendor + funded account exist
  - the whole pipeline (checkout -> queue -> processing batch job) is
    fully exercisable and testable today, vendor or not
  - swapping in a real vendor later is a one-line env var change
    (LETTER_PROVIDER=stannp, etc.) plus filling in that provider's send()
    -- nothing in database.py or the checkout/redemption flows changes.

Candidates worth comparing when it's time to pick one (UK letter-sending
APIs, roughly this shape -- not vetted, not a recommendation, just the
usual names to compare on price/turnaround/tracking):
  Stannp, PostGrid, Lob (US-first but does UK), Docmail/CFH Docmail,
  Click2Mail. Whichever is chosen, its provider class below only needs to
  implement send() -- see StannpLetterProvider for the shape to follow
  (NOT wired to a real API key or endpoint -- placeholder only).
=============================================================================
"""

import os
import logging
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("vector-data-labs")


@dataclass
class LetterSendResult:
    ok: bool
    status: str                          # e.g. "sent", "queued_dry_run", "failed"
    provider_name: str
    provider_reference: Optional[str] = None
    error: Optional[str] = None


class LetterProvider:
    """Base interface. A dispatch dict has: reference, address,
    applicant_name (may be None), sale_context (single_purchase /
    subscription_dispatch / free_lead)."""

    name = "base"

    def send(self, dispatch: dict) -> LetterSendResult:
        raise NotImplementedError


class ConsoleLetterProvider(LetterProvider):
    """Safe default. Sends nothing, spends nothing, logs what would have
    gone out so the queue/processing pipeline is fully testable before a
    real vendor is chosen and funded. Marks status 'queued_dry_run' (not
    'sent') so it's never confused with a real, verifiable postal send in
    letter_dispatches -- a report built from this data must be able to
    tell "we actually mailed this" from "we only logged that we would
    have"."""

    name = "console"

    def send(self, dispatch: dict) -> LetterSendResult:
        logger.info(
            f"[Letter Dispatch][DRY RUN] Would send Article 14 notice letter -- "
            f"ref={dispatch['reference']} to={dispatch['applicant_name'] or '(name unknown)'} "
            f"address={dispatch['address']!r} context={dispatch['sale_context']}"
        )
        return LetterSendResult(ok=True, status="queued_dry_run", provider_name=self.name)


class StannpLetterProvider(LetterProvider):
    """PLACEHOLDER -- not wired to a real API key or endpoint. Shows the
    shape a real vendor integration takes; fill in once a vendor is chosen
    and an account/API key exist. Reads STANNP_API_KEY from the
    environment the same way every other integration in this codebase
    reads its credentials (os.getenv, never hardcoded) -- see
    database.py's SUPABASE_DB_URL / research.py's GOOGLE_MAPS_KEY for the
    existing pattern this follows."""

    name = "stannp"

    def __init__(self):
        self.api_key = os.getenv("STANNP_API_KEY", "").strip()

    def send(self, dispatch: dict) -> LetterSendResult:
        if not self.api_key:
            return LetterSendResult(ok=False, status="failed", provider_name=self.name,
                                     error="STANNP_API_KEY not set")
        # TODO once vendor is confirmed: build the real HTML/PDF letter
        # content (generate_homeowner_letter's template, or a dedicated
        # print-ready version of it), POST to the vendor's create-letter
        # endpoint, and map their response into LetterSendResult. Left
        # unimplemented deliberately -- do not wire this to a real HTTP
        # call against a guessed endpoint shape.
        raise NotImplementedError(
            "StannpLetterProvider.send() is a placeholder -- implement against "
            "the real API once Stannp (or whichever vendor) is actually chosen."
        )


_PROVIDERS = {
    "console": ConsoleLetterProvider,
    "stannp": StannpLetterProvider,
}


def get_letter_provider() -> LetterProvider:
    """LETTER_PROVIDER env var picks the provider; defaults to 'console'
    (safe/dry-run) rather than defaulting to any real vendor -- a real
    send should only ever happen because someone deliberately set this,
    never by default. Unknown/unset value also falls back to console with
    a warning, rather than raising, so a missing env var in a new
    deployment fails safe (nothing sent, nothing charged) instead of
    crashing the batch job."""
    choice = os.getenv("LETTER_PROVIDER", "console").strip().lower()
    provider_cls = _PROVIDERS.get(choice)
    if provider_cls is None:
        logger.warning(f"[Letter Dispatch] Unknown LETTER_PROVIDER={choice!r}, falling back to console (dry-run).")
        provider_cls = ConsoleLetterProvider
    return provider_cls()
