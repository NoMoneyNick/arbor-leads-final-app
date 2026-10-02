"""
letter_providers/base.py -- The adapter interface every postal provider
implements, plus the shared result/outcome vocabulary.

Design directly reuses the pattern already proven in
standalone_mailer/mailer.py (tested locally this session -- see
tests/test_providers.py): a stable idempotency key, a content fingerprint
that blocks silent content drift on retry, and an explicit "unknown" outcome
for anything ambiguous (timeout, malformed response, crash) rather than
guessing sent-or-not.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Optional


# The only outcomes a provider adapter is allowed to report. Deliberately
# small and shared across every adapter so letter_providers.registry can
# reason about them uniformly.
OUTCOME_ACCEPTED = "accepted"     # provider confirmed it will print/post this
OUTCOME_DISPATCHED = "dispatched" # provider confirmed the item has left their system
OUTCOME_REJECTED = "rejected"     # provider confirmed it will NOT send this (bad address, account issue, etc.)
OUTCOME_UNKNOWN = "unknown"       # timeout / malformed response / crash -- do not resend automatically
VALID_OUTCOMES = (OUTCOME_ACCEPTED, OUTCOME_DISPATCHED, OUTCOME_REJECTED, OUTCOME_UNKNOWN)


@dataclass(frozen=True)
class LetterRequest:
    """Everything a provider needs to submit one letter. `content_html` is
    the FULL rendered letter (from letter_content.render_letter) -- adapters
    must submit this exact content, never re-derive their own copy, so the
    fingerprint recorded against the obligation is provably what was sent."""
    idempotency_key: str
    lead_reference: str
    address_lines: dict         # {"line1":..., "city":..., "postcode":..., "country": "GB"}
    applicant_name: Optional[str]
    content_html: str
    content_fingerprint: str    # sha256 of content_html, computed by the caller -- adapters must not recompute differently


@dataclass(frozen=True)
class ProviderResult:
    outcome: str                # one of VALID_OUTCOMES
    provider_name: str
    provider_reference: Optional[str] = None
    cost_pence: Optional[int] = None
    message: str = ""
    # Set True ONLY by an adapter that can PROVE this rejection did not
    # charge/consume anything (so a reserved budget may be released when it
    # is learned later via check_status). Default False = "rejected, but
    # charge not proven absent" -- reconciliation then holds the reservation
    # for manual review instead of releasing it. No real adapter sets this
    # today (Intelliprint statuses such as 'returned'/'cancelled' do not
    # prove "uncharged").
    confirmed_uncharged: bool = False

    def __post_init__(self):
        if self.outcome not in VALID_OUTCOMES:
            raise ValueError(f"Invalid outcome {self.outcome!r}, must be one of {VALID_OUTCOMES}")


@dataclass(frozen=True)
class ReferenceLookup:
    """Result of LetterProviderAdapter.find_by_reference(): the provider's
    jobs carrying exactly our submission reference.
    complete=True ONLY when the provider's own paging/count semantics show
    the WHOLE result set was read; otherwise complete=False and `reason` says
    why (matches is then not to be trusted, and an empty `matches` must NOT be
    read as "nothing was submitted")."""
    complete: bool
    matches: tuple = ()          # tuple of ProviderResult, one per matching provider job
    reason: str = ""


def fingerprint_content(content_html: str) -> str:
    return hashlib.sha256(content_html.encode("utf-8")).hexdigest()


class ProviderCapabilities:
    """What an adapter can and can't handle, used by the registry to reject
    an unsuitable request BEFORE calling send() rather than passing it
    through and hoping. Section 6: 'invalid content, suppressed recipients
    and invalid addresses should not be passed around providers to bypass
    validation' -- capability/validation checks happen once, centrally, in
    letter_providers.registry.attempt_send, not per-adapter."""
    def __init__(self, *, max_pages: int = 2, countries: tuple = ("GB",),
                 supports_colour: bool = True, max_cost_pence: Optional[int] = None):
        self.max_pages = max_pages
        self.countries = countries
        self.supports_colour = supports_colour
        self.max_cost_pence = max_cost_pence


class LetterProviderAdapter:
    """Base interface. Every real adapter implements send() and status().
    is_configured() must be accurate -- the registry uses it to skip an
    adapter that has no credentials rather than attempting a call that can
    only fail, so an unconfigured adapter never becomes the reason a letter
    sits in 'submitting' against a dead endpoint."""

    name = "base"
    capabilities = ProviderCapabilities()
    # True only for an adapter that implements find_by_reference() against a
    # provider that documents a search by our own reference.
    supports_reference_lookup = False

    def find_by_reference(self, idempotency_key: str, *, testmode: bool) -> Optional["ReferenceLookup"]:
        """Read-only search for jobs already submitted under the reference
        derived from `idempotency_key`, in the given test/live mode. Used only
        to recover an 'unknown' obligation that has no saved provider
        reference. Never sends. Returns None if unsupported."""
        return None

    def is_configured(self) -> bool:
        raise NotImplementedError

    def send(self, request: LetterRequest) -> ProviderResult:
        raise NotImplementedError

    def check_status(self, provider_reference: str) -> Optional[ProviderResult]:
        """Best-effort reconciliation lookup for a previously-submitted
        reference, used to resolve an 'unknown' outcome without resending.
        Returns None if the adapter has no status-lookup capability."""
        return None
