"""
letter_providers/intelliprint_provider.py -- real adapter for Intelliprint
(https://api.intelliprint.net/v1), built 2026-09-26 once Nick confirmed
Intelliprint account access was fixed and chosen as the first real
provider (see ERROR_LOG.md's 2026-09-24 comparison entry and
docs/operator_guide.md Section 23 for the prior PC2Paper-vs-Intelliprint
research this adapter reuses rather than repeats).

VERIFIED AGAINST INTELLIPRINT'S CURRENT LIVE DOCUMENTATION THIS PASS
(2026-09-26, via WebFetch against intelliprint.net -- not recalled from
training data, not assumed from the 2026-09-24 research alone, which did
not include the exact request/response field names below):

  - POST https://api.intelliprint.net/v1/prints
    Auth: "Authorization: Bearer <API key>" (per intelliprint.net/api).
    Body is application/x-www-form-urlencoded (Intelliprint's own create
    example uses curl -d, i.e. form-urlencoded, despite the reference
    page separately describing multipart/form-data for the file-upload
    case we don't use) -- same style already used by stannp_provider.py.
    Fields actually used here: content, type=letter, reference,
    testmode, confirmed, recipients[0][address][name/line/postcode/country].
    `content` accepts plain text/HTML directly -- confirmed from
    Intelliprint's own create-endpoint example -- so this adapter submits
    request.content_html as-is; no PDF rendering step is needed or added
    here (letter_content.py deliberately has no such runtime dependency).
    `recipients[0][address][line]` is a SINGLE field -- Intelliprint's own
    example folds street+town into one comma-joined string
    ("123 Main St, Anytown, Anyplace"), which is what this adapter does
    with address_lines['line1']/['city'].

  - Address-window positioning: VERIFIED NOT APPLICABLE to our content.
    intelliprint.net/design-specs states plainly that Intelliprint prints
    the recipient address separately from the submitted document, from
    the recipients[].address data supplied above -- "you don't include
    this in your design file." The 23mm-from-left/43mm-from-top C5 window
    figure found in the 2026-09-24 research describes what INTELLIPRINT
    itself prints at that position, not a zone our rendered letter needs
    to leave blank or target. letter_content.py's actual page margins
    (16mm/18mm padding on a 210x297mm page) sit comfortably inside
    Intelliprint's documented 204x291mm safe zone regardless, so no
    change was needed in letter_content.py for this integration.

  - Status lifecycle (GET /v1/prints/{id} for check_status, and the same
    values appear on the create response): draft -> waiting_to_print ->
    printing -> enclosing -> shipping -> sent, plus returned / cancelled /
    invalid_address. No "delivered" status exists for untracked post.
    Mapped to this codebase's outcome vocabulary (letter_providers/base.py):
      draft/waiting_to_print/printing/enclosing/shipping -> ACCEPTED
        (provider has taken this job into its pipeline and not rejected it)
      sent -> DISPATCHED (confirmed left Intelliprint's system)
      returned/cancelled/invalid_address -> REJECTED (confirmed non-send)
      anything else / missing -> UNKNOWN (never guess)
    A parseable {"error": {...}} response (documented error types:
    invalid_request_error, authentication_error, payment_error,
    rate_limited, internal_error) is mapped to REJECTED, matching
    letter_providers/base.py's own OUTCOME_REJECTED comment ("bad
    address, account issue, etc.") -- these are confirmed non-sends, not
    ambiguous ones. Network/timeout/malformed-JSON is mapped to UNKNOWN,
    exactly as stannp_provider.py already does, since we genuinely do not
    know whether Intelliprint received/accepted the request.

  - testmode=true (this adapter's default, INTELLIPRINT_TEST_MODE env
    var, same convention as STANNP_TEST_MODE): "Test mode print jobs are
    not charged for and are not actually sent out" but still run "the
    full pipeline -- validation, address verification, pricing
    calculation, webhook dispatch" (intelliprint.net/api). This is
    independent of, and in addition to, this codebase's own
    fulfilment.letter_sending_live() three-way gate and worker.py's
    is_dry_run -- real posting stays off via BOTH layers until Nick
    explicitly changes them.

  - Idempotency: NOT documented by Intelliprint (confirmed again this
    pass; `reference` is described only as a "user-friendly description").
    This adapter does not depend on provider-side idempotency --
    letter_providers/registry.py::attempt_send already guarantees an
    obligation is claimed exactly once per attempt and never auto-resends
    on an ambiguous outcome; see that module's own docstring.

NOT YET EMPIRICALLY VERIFIED (no live account access from this sandbox):
  the exact initial `status` a test-mode+confirmed=true submission
  actually returns, and the exact shape of an error response body in
  practice. Covered by unit tests against the documented shapes above;
  a real test-mode call against Nick's account is the next step (see
  scripts/intelliprint_test_send.py and the report accompanying this
  change for how to run it without ever pasting the API key into chat).
"""
from __future__ import annotations

import json
import os
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from letter_providers.base import (
    LetterProviderAdapter, LetterRequest, ProviderResult, ProviderCapabilities,
    OUTCOME_ACCEPTED, OUTCOME_DISPATCHED, OUTCOME_REJECTED, OUTCOME_UNKNOWN,
)

API_BASE = "https://api.intelliprint.net/v1"

_ACCEPTED_STATUSES = {"draft", "waiting_to_print", "printing", "enclosing", "shipping"}
_DISPATCHED_STATUSES = {"sent"}
_REJECTED_STATUSES = {"returned", "cancelled", "invalid_address"}


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _map_status(status: str) -> str:
    if status in _ACCEPTED_STATUSES:
        return OUTCOME_ACCEPTED
    if status in _DISPATCHED_STATUSES:
        return OUTCOME_DISPATCHED
    if status in _REJECTED_STATUSES:
        return OUTCOME_REJECTED
    return OUTCOME_UNKNOWN


def _cost_pence(data: dict) -> "int | None":
    cost = data.get("cost") or {}
    after_tax = cost.get("after_tax")
    if after_tax is None:
        return None
    # Intelliprint's own docs: "All costs amounts need to be divided by
    # 10^8 to get the real GBP price." -> pence = after_tax / 10**8 * 100
    return round(after_tax / 1_000_000)


class IntelliprintProvider(LetterProviderAdapter):
    name = "intelliprint"
    capabilities = ProviderCapabilities(max_pages=2, countries=("GB",))

    def __init__(self):
        self.api_key = os.getenv("INTELLIPRINT_API_KEY", "").strip()
        self.test_mode = os.getenv("INTELLIPRINT_TEST_MODE", "true").strip().lower() != "false"

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        }

    def send(self, request: LetterRequest) -> ProviderResult:
        if not self.is_configured():
            # Should never be reached -- the registry checks is_configured()
            # first -- but fail safe (unknown, not accepted) if it is.
            return ProviderResult(outcome=OUTCOME_UNKNOWN, provider_name=self.name,
                                   message="INTELLIPRINT_API_KEY not set.")

        addr = request.address_lines
        line = ", ".join(part for part in (addr.get("line1", ""), addr.get("city", "")) if part)
        payload = {
            "type": "letter",
            "content": request.content_html,
            "reference": f"treekey_{request.idempotency_key[:48]}",
            "testmode": "true" if self.test_mode else "false",
            "confirmed": "true",
            "recipients[0][address][name]": request.applicant_name or "",
            "recipients[0][address][line]": line,
            "recipients[0][address][postcode]": addr.get("postcode", ""),
            "recipients[0][address][country]": addr.get("country", "GB"),
        }
        http_request = Request(
            f"{API_BASE}/prints",
            data=urlencode(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with build_opener(_NoRedirects()).open(http_request, timeout=30) as response:
                data = json.loads(response.read(2_000_000).decode())
        except Exception as exc:
            # Timeout, HTTP error, malformed body -- may have happened
            # AFTER Intelliprint accepted the request. Never assume
            # rejection or acceptance here -- see this module's own
            # docstring on why network/timeout errors map to UNKNOWN.
            return ProviderResult(outcome=OUTCOME_UNKNOWN, provider_name=self.name,
                                   message=f"Ambiguous outcome, do not resend without reconciling: {type(exc).__name__}: {exc}")

        if not isinstance(data, dict):
            return ProviderResult(outcome=OUTCOME_UNKNOWN, provider_name=self.name,
                                   message="Non-object response body -- cannot interpret, held for reconciliation.")

        if "error" in data:
            err = data.get("error") or {}
            return ProviderResult(outcome=OUTCOME_REJECTED, provider_name=self.name,
                                   message=f"Intelliprint reported an error: {err.get('type', 'unknown')}: {err.get('message', '')}")

        letters = data.get("letters") or []
        status = (letters[0].get("status") if letters and isinstance(letters[0], dict) else None) or data.get("status")
        outcome = _map_status(status or "")
        return ProviderResult(
            outcome=outcome, provider_name=self.name,
            provider_reference=str(data.get("id", "")) or None,
            cost_pence=_cost_pence(data),
            message=f"Intelliprint status={status!r} (testmode={self.test_mode}).",
        )

    def check_status(self, provider_reference: str) -> "ProviderResult | None":
        """Best-effort reconciliation lookup -- see base.py's own docstring
        on when this is used (resolving a prior 'unknown' outcome without
        resending). Returns None on any failure rather than raising, since
        callers treat None as 'still unresolved,' not as a new outcome."""
        if not self.is_configured() or not provider_reference:
            return None
        http_request = Request(
            f"{API_BASE}/prints/{provider_reference}",
            headers=self._headers(),
            method="GET",
        )
        try:
            with build_opener(_NoRedirects()).open(http_request, timeout=30) as response:
                data = json.loads(response.read(2_000_000).decode())
        except Exception:
            return None

        if not isinstance(data, dict) or "error" in data:
            return None

        letters = data.get("letters") or []
        status = (letters[0].get("status") if letters and isinstance(letters[0], dict) else None) or data.get("status")
        return ProviderResult(
            outcome=_map_status(status or ""), provider_name=self.name,
            provider_reference=str(data.get("id", provider_reference)),
            cost_pence=_cost_pence(data),
            message=f"Reconciled via check_status: status={status!r}.",
        )
