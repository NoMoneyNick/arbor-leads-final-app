"""
letter_providers/stannp_provider.py -- Adapted from standalone_mailer/mailer.py's
already-reviewed transport (stannp_transport, mailer.py:54-66) and
send() design (mailer.py:113-172), NOT written from scratch.

STATUS: request/response shape only. No live account, no API key exists in
this session, and I have not re-verified Stannp's endpoint/field names
against their current documentation this session -- the endpoint URL and
payload keys below are exactly what standalone_mailer/mailer.py already
used (that file's own header says the same about its provenance). Treat
this as "the same unverified-against-live-API placeholder shape the
existing codebase already had," carried into the new adapter interface,
not as a newly-confirmed integration. is_configured() correctly reports
False with no STANNP_API_KEY set, so the registry will skip this adapter
entirely rather than attempt a call that can only fail.

Before this can be marked complete: confirm current field names/endpoint
against Stannp's own current API docs, obtain and fund a real account, and
run at least one real 'test' mode submission (Stannp's own test flag, not
this codebase's dry-run) to observe real response shapes.
"""
from __future__ import annotations

import base64
import json
import os
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from letter_providers.base import (
    LetterProviderAdapter, LetterRequest, ProviderResult, ProviderCapabilities,
    OUTCOME_ACCEPTED, OUTCOME_REJECTED, OUTCOME_UNKNOWN,
)


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class StannpProvider(LetterProviderAdapter):
    name = "stannp"
    capabilities = ProviderCapabilities(max_pages=2, countries=("GB",))

    def __init__(self):
        self.api_key = os.getenv("STANNP_API_KEY", "").strip()
        self.template_id = os.getenv("STANNP_TEMPLATE_ID", "").strip()

    def is_configured(self) -> bool:
        return bool(self.api_key and self.template_id)

    def send(self, request: LetterRequest) -> ProviderResult:
        if not self.is_configured():
            # Should never be reached -- registry checks is_configured()
            # first -- but fail safe (unknown, not accepted) if it is.
            return ProviderResult(outcome=OUTCOME_UNKNOWN, provider_name=self.name,
                                   message="STANNP_API_KEY/STANNP_TEMPLATE_ID not set.")

        payload = {
            "test": "1" if os.getenv("STANNP_TEST_MODE", "true").strip().lower() != "false" else "0",
            "template": self.template_id,
            "size": "A4", "duplex": "0", "clearzone": "1", "post_unverified": "0",
            "tags": f"treekey_{request.idempotency_key[:32]}",
        }
        addr = request.address_lines
        payload.update({
            "recipient[address1]": addr.get("line1", ""),
            "recipient[city]": addr.get("city", ""),
            "recipient[postcode]": addr.get("postcode", ""),
            "recipient[country]": addr.get("country", "GB"),
        })
        token = base64.b64encode((self.api_key + ":").encode()).decode()
        http_request = Request(
            "https://api-eu1.stannp.com/v1/letters/create",
            data=urlencode(payload).encode(),
            headers={"Authorization": "Basic " + token,
                     "Content-Type": "application/x-www-form-urlencoded",
                     "Accept": "application/json"},
            method="POST",
        )
        try:
            with build_opener(_NoRedirects()).open(http_request, timeout=30) as response:
                data = json.loads(response.read(2_000_000).decode())
        except Exception as exc:
            # Timeout, HTTP error, malformed body -- may have happened AFTER
            # the provider accepted the request. Never assume rejection or
            # acceptance here.
            return ProviderResult(outcome=OUTCOME_UNKNOWN, provider_name=self.name,
                                   message=f"Ambiguous outcome, do not resend without reconciling: {type(exc).__name__}")

        if not isinstance(data, dict) or not data.get("success"):
            return ProviderResult(outcome=OUTCOME_REJECTED, provider_name=self.name,
                                   message="Stannp reported failure/rejection.")
        inner = data.get("data") or {}
        return ProviderResult(outcome=OUTCOME_ACCEPTED, provider_name=self.name,
                               provider_reference=str(inner.get("id", "")),
                               cost_pence=int(float(inner.get("cost", 0)) * 100) if inner.get("cost") else None,
                               message="Accepted by Stannp (test mode unless STANNP_TEST_MODE=false).")
