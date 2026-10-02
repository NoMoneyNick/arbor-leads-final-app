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
    Body is application/x-www-form-urlencoded throughout -- including the
    file upload (see eighth fix below: `file[content]` takes BASE64 file
    data, per reference/prints/create's documented base64 upload shape,
    which stays form-urlencoded rather than needing an actual
    multipart/form-data request) -- same convention already used by
    stannp_provider.py.
    SUPERSEDED by the eighth fix below: originally submitted `content`
    (raw request.content_html, no rendering step) + `recipients[...]`.
    Now submits a rendered PDF via `file[content]`/`file[name]` instead,
    with `recipients[...]` omitted -- see that entry for why. This
    paragraph is kept for the general auth/body-format facts, which are
    still accurate.
    `recipients[0][address][line]` WAS a single field the adapter built by
    comma-joining `address_lines['line1']`/`['city']` -- kept only as
    history; no longer sent (see eighth fix).

  - Address-window positioning: SUPERSEDED this pass (2026-09-26, fifth
    fix) -- see that entry below. The "VERIFIED NOT APPLICABLE" claim this
    paragraph originally made (that Intelliprint overlays the address from
    recipients[] data and "you don't include this in your design file")
    turned out to be drawn from intelliprint.net's general design-specs
    marketing page, which conflicts with Intelliprint's own downloadable,
    print-ready A4 letter template -- the more specific and authoritative
    artifact for exactly this question. The template resolves the conflict
    the other way: the sender DOES render its own address text, in an
    exact documented position, and Intelliprint reads it from there (OCR
    through the window), not overlays it digitally. letter_content.py now
    renders its address block at that exact position -- see that module's
    own 2026-09-26 fix comment and the fifth-fix entry below for the full
    story and the discrepancy this corrects.

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

2026-09-26, second fix -- HTTP error responses were being swallowed.
`urllib.request.urlopen`/`opener.open()` raises `urllib.error.HTTPError`
for any non-2xx response BEFORE this module ever got to read the body --
so a real rejection from Intelliprint (which, per intelliprint.net/docs/errors,
returns a documented `{"error": {"type","code","message","param"}}` JSON
body even on a 4xx) was being caught by the generic `except Exception`
below and reported as a bare "HTTP Error 403: Forbidden" with the actual
diagnostic body and headers never read at all. Fixed: `HTTPError` is now
caught specifically, its body is read and, if it parses as Intelliprint's
own documented error shape, routed through the exact same REJECTED path
a well-formed 200 response with an "error" key would use (this is a
genuine confirmed-by-the-provider rejection, not an ambiguous one -- see
OUTCOME_REJECTED's mapping above). If the body does NOT parse as that
shape (e.g. HTML from a security/WAF layer in front of the API, which
would look nothing like Intelliprint's own JSON error format), the
outcome stays UNKNOWN, but the raw (truncated, non-secret) body and a
small allow-list of response headers are now captured in the message so
a human can actually distinguish "Intelliprint's app rejected this
request" from "something in front of Intelliprint blocked it" -- which
this module cannot safely decide on its own.

Per intelliprint.net/docs/errors, the specific `code: "forbidden"` (403)
means "the API key provided was not authorised to access the requested
resource" -- distinct from `invalid_api_key`/`no_api_key` (401, a bad or
missing key entirely). A 403 with that documented shape therefore usually
means the key syntax/auth itself worked but the account or key lacks a
required permission/scope for POST /v1/prints specifically -- worth
checking with a read-only call first (see scripts/intelliprint_auth_check.py,
which calls the documented `GET /v1/prints` list endpoint -- no print job
is created, no charge, no send, regardless of testmode).

2026-09-26, third fix -- no User-Agent header was ever set (see
_headers() below), so Intelliprint's own Cloudflare front end could reject
requests outright as browser_signature_banned (error 1010) before
Intelliprint's application code saw them at all -- a different failure
mode from anything covered by the second fix above. Fixed with an honest,
non-impersonating application identifier only; no browser spoofing.

2026-09-26, fourth fix -- Nick's first real test-mode PDF (once the third
fix cleared the Cloudflare block) showed visible right-edge clipping and
what looked like a separate address sheet making three pages instead of
two, and asked for the actual submitted wording to be identified rather
than assumed approved. Re-verified against Intelliprint's current
documentation (reference/prints/create, docs/submitting-print-jobs):
`printing.double_sided` defaults to "no" (simplex) and `add_address_sheet`
defaults to false ("address printed on the same page as your letter") --
NEITHER was ever set explicitly in send()'s payload, so this letter's two
HTML pages (front introduction / reverse privacy notice) were being sent
to print as two separate one-sided sheets, not one sheet printed front and
back as the two-page design intends. Both are now set explicitly (see
send() below) rather than relying on undocumented-to-us defaults. The
right-edge clipping is addressed separately, in letter_content.py's own
CSS (a genuinely missing `@page` rule -- see that module's 2026-09-26 fix
comment) rather than here. Also added: `_pdf_preview_url()`, surfacing
Intelliprint's own documented signed `pdf` preview URL (from
reference/prints/retrieve's response shape) in ProviderResult.message, so
the actual provider-rendered PDF can be fetched and inspected directly
instead of assuming successful submission means correct rendering.

2026-09-26, fifth fix -- Nick's follow-up correctly rejected the fourth
fix's `add_address_sheet=false` as "not evidence of a fix" for the
three-page PDF, since that was already Intelliprint's documented default
(setting a parameter to its own default cannot have changed observed
behaviour). Re-investigated properly rather than re-asserting the same
config:

  - The actual, evidenced cause of the three-page PDF is the missing
    `@page` CSS rule identified and fixed in letter_content.py (2026-09-26
    fix, that module) -- without it, an HTML-to-PDF engine falls back to
    its own default paper size instead of the 210mm page this letter's
    HTML assumes, which is consistent with BOTH symptoms Nick originally
    reported together (right-edge clipping AND an extra page) from a
    single root cause, rather than needing two unrelated explanations.
    This is stated as a reasoned, evidenced hypothesis, not confirmed
    fact -- confirming it requires comparing against the actual PREVIOUS
    (pre-fix) Intelliprint-rendered PDF, which this adapter does not have
    a copy of; if Nick still has that original PDF or the response it came
    from, it would let this be checked directly instead of inferred.

  - The address-window conflict this docstring previously left open is
    now resolved, not just flagged -- see the "Address-window positioning"
    entry above and letter_content.py's own fix comment. Intelliprint's
    official downloadable A4 template (the specific, authoritative
    artifact, checked in preference to the general design-specs page that
    an earlier pass relied on) documents an exact address clear zone
    (40mm from left, 20mm from top) that the sender fills in and
    Intelliprint reads for the window envelope -- not an API-side overlay.
    letter_content.py's front page now positions its address block there
    exactly. `add_address_sheet=false` remains correct under this
    resolution (the address stays on the same page as the letter, per the
    template) -- it just was never, on its own, evidence that the 3-page
    bug was fixed, which is the distinction Nick's correction was about.

  - Added `_pages_sheets_note()`, surfacing Intelliprint's documented
    `pages`/`sheets` response fields (reference/prints/create) in
    ProviderResult.message: "two PDF pages alone do not establish
    front-and-back printing" (Nick's words) -- pages=2/sheets=1 in a real
    response IS that evidence; pages=2/sheets=2 would mean duplex did not
    actually happen despite the request payload asking for it. Not
    asserted here (no live call from this sandbox) -- surfaced so the next
    real test-mode submission's own console output shows it.

2026-09-26, seventh fix -- Nick's first real submission attempt after the
sixth fix returned HTTP 400 `parameter_unknown`: "Received unknown
parameter: add_address_sheet". Re-checked reference/prints/create's actual
REQUEST body schema directly (not re-derived from the earlier passes'
reading of it, which never explicitly confirmed this field was
request-side): `add_address_sheet` is documented ONLY in the RESPONSE
schema ("whether the address is printed on a separate page followed by
your letter (if true) or... the same page as your letter (if false)") --
it describes what Intelliprint decided to do, not a caller-settable
input. It does not exist under any other name or nesting in the request
schema either (the full accepted request field list was checked, not
guessed at) -- so there is no supported way to request "same page" or
"separate sheet" directly; whatever governs it is presumably inferred by
Intelliprint from the submitted content/recipients data. Removed from
send()'s payload rather than relocated. See that function's own comment
for the full accepted-field list this was checked against. The fourth and
sixth fixes' framing of `add_address_sheet=false` as intentional request
configuration was mistaken on this specific point; both are superseded by
this entry for that field. `printing[double_sided]=yes` is unaffected --
re-confirmed this same pass as a genuine, documented, nested request
field (printing[double_sided], accepts "no"/"yes"/"mixed", default "no").
Also re-checked (not used, not needed for this fix, noted for
completeness): a documented `address_window` request field exists
("left"/"right", default "left") controlling which side of the letter the
envelope window is on -- separate from address positioning within the
page, which is letter_content.py's own CSS concern, and left at its
default since nothing indicates our left-positioned address block needs
a non-default window side.

2026-09-26, eighth fix -- the seventh fix's submission (HTML `content` +
`recipients[]`, no `add_address_sheet`) actually reached Intelliprint and
returned a real PDF, and Nick reported it: still 3 pages. `pages=3`,
`sheets=2`, `add_address_sheet=false` in the response. Investigated the
actual submitted content against the actual provider PDF, per the ask, not
assumed from settings alone:

  - Extracted the provider PDF page-by-page (pdftotext -f/-l per page).
    Physical page 1 contains ONLY a recipient name/address block plus a
    barcode/mailmark marker -- no letter content at all. Physical pages 2
    and 3 are our own two HTML pages (introduction, then privacy),
    correctly duplexed onto one sheet together (the barcode markers read
    "S1/2" for page 1 and "S2/2" for both pages 2 and 3 -- i.e. page 1 is
    genuinely its own separate physical SHEET, and pages 2+3 are correctly
    printed front-and-back on the other sheet: duplex for OUR content is
    confirmed working; the extra sheet is the entire remaining problem).
  - The text on that leading page ("Sample Homeowner" / "123 Sample
    Street, Sample Town" / "ST1 2AB") is an EXACT match for
    `request.applicant_name` and `request.address_lines` -- i.e.
    `recipients[0][address][name/line/postcode]` -- and does NOT appear
    anywhere in our submitted HTML (which prints a generic "TO THE
    PROPERTY OWNER / OCCUPIER" label, never a name). This is conclusive,
    not inferred: the leading page was built by Intelliprint FROM the
    `recipients[]` API data, not from anything in our HTML/CSS -- our
    `.letter-page` CSS has no rule capable of inserting a page BEFORE the
    first div's own content in any case (`page-break-after` only affects
    what comes after a div, and there is nothing preceding the first div
    for a `page-break-before` to apply to), so a CSS-side cause was ruled
    out by inspection, not just by this evidence.
  - Re-checked Intelliprint's "Choose a Content Strategy" documentation
    (not previously read this closely): submitting via `content` (HTML
    text) is a DIFFERENT content strategy from submitting a pre-rendered
    `file`, and the two behave differently for addressing. Quoted
    verbatim: "If your file for a letter already has an address placed in
    the correct position, you do not need to provide any recipients
    separately, Intelliprint will read the recipient address directly
    from the file." No equivalent statement exists for the `content`
    (HTML) strategy -- recipients must always be supplied separately for
    it, and (per the evidence above) Intelliprint generates its own
    address-bearing page from that data rather than reading one from the
    HTML. This also resolves an open question from the sixth fix: the
    official A4_Template.pdf's "put the recipient's address here, we read
    it from this area" instructions describe the FILE (pre-rendered PDF)
    content strategy specifically -- not the HTML content strategy this
    adapter was actually using, which is why positioning our own address
    block correctly never stopped the extra page. The sixth fix's
    positioning work was not wasted, though -- it is exactly what the file
    strategy needs, and needed no further change.
  - CONCLUSION: HTML submission cannot reliably avoid this extra page --
    it is how that content strategy is documented to work, not a bug in
    our request. Per Nick's own instruction to assess the file route in
    that case, switched `send()` to render `letter_content.py`'s HTML to a
    real PDF (via Playwright/Chromium -- see the new
    `_render_html_to_pdf_bytes()`, using the exact settings already
    verified by tests/letter_pagination_check/run_pagination_check.py) and
    submit it via `file[content]`/`file[name]` (base64-encoded, per
    reference/prints/create's documented base64 upload shape -- stays
    form-urlencoded, no multipart request needed). `recipients[...]` is
    now omitted entirely, matching the file strategy's documented usage
    exactly, so the next test isolates this one change. THIS IS A NEW
    PRODUCTION DEPENDENCY (Playwright + a downloaded Chromium build,
    see requirements.txt) wherever this code actually sends live letters,
    not only in a test environment -- flagged plainly, not glossed over.

STILL NOT independently verified (needs a real test-mode submission on
Nick's machine that actually reaches a successful response, per the
accompanying report): that a `file`-based submission (a) produces exactly
2 pages on 1 sheet with no extra leading page, (b) that Intelliprint's OCR
actually reads our `.address-clear-zone` text correctly rather than
requiring recipients data we're no longer sending, and (c) whether real
(non-test) sends will need `recipients[]` restored for postage-cost
calculation despite the documented "you do not need to provide" language
-- worth confirming with Intelliprint support before live sending is ever
enabled. All three are designed-for and reasoned-through from Intelliprint's
own documentation and this pass's own evidence, not assumed confirmed.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.error
from dataclasses import replace as _dc_replace
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

from letter_providers.base import (
    LetterProviderAdapter, LetterRequest, ProviderResult, ProviderCapabilities, ReferenceLookup,
    OUTCOME_ACCEPTED, OUTCOME_DISPATCHED, OUTCOME_REJECTED, OUTCOME_UNKNOWN,
)

API_BASE = "https://api.intelliprint.net/v1"

_ACCEPTED_STATUSES = {"draft", "waiting_to_print", "printing", "enclosing", "shipping"}
_DISPATCHED_STATUSES = {"sent"}
_REJECTED_STATUSES = {"returned", "cancelled", "invalid_address"}

# Response headers safe to surface in a diagnostic message -- an allow-list,
# not a block-list, so a new header we've never seen defaults to hidden
# rather than accidentally shown. None of these can ever carry the API key,
# a cookie, or any personal/homeowner data -- they're transport/diagnostic
# metadata about the HTTP exchange itself. Deliberately excludes
# WWW-Authenticate even though it's a common, non-secret diagnostic header:
# Nick's instruction was to exclude "authorization headers" outright, and
# the blocked-substrings check below would silently drop it anyway (it
# contains "auth"), so it's left out of this list too rather than listing
# something that can never actually appear.
_SAFE_DIAGNOSTIC_HEADERS = (
    "content-type", "date", "server", "retry-after",
    "x-request-id", "request-id", "x-amzn-requestid", "cf-ray", "x-ratelimit-limit",
    "x-ratelimit-remaining",
)
_MAX_BODY_CHARS = 2000  # plenty for a diagnostic snippet; caps pathological bodies


class _NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _safe_response_headers(headers) -> dict:
    """headers: an http.client.HTTPMessage (from HTTPError.headers or a
    successful response's .headers). Returns only the allow-listed subset,
    keyed by their original casing from the response. Belt-and-braces: even
    though the allow-list above contains nothing auth/secret-shaped, this
    also explicitly skips any header whose name contains a sensitive
    substring, in case a future edit widens the allow-list carelessly."""
    if not headers:
        return {}
    blocked_substrings = ("auth", "cookie", "key", "token", "secret")
    out = {}
    for name in headers.keys():
        lname = name.lower()
        if lname in _SAFE_DIAGNOSTIC_HEADERS and not any(b in lname for b in blocked_substrings):
            out[name] = headers.get(name)
    return out


def _read_http_error_body(exc: "urllib.error.HTTPError") -> str:
    try:
        raw = exc.read(_MAX_BODY_CHARS + 1)
    except Exception:
        return "(could not read response body)"
    try:
        text = raw.decode("utf-8", errors="replace")
    except Exception:
        return "(non-text response body)"
    if len(text) > _MAX_BODY_CHARS:
        text = text[:_MAX_BODY_CHARS] + "... (truncated)"
    return text


def _classify_http_error(exc: "urllib.error.HTTPError", provider_name: str) -> ProviderResult:
    """Turns a raised HTTPError into a ProviderResult instead of letting it
    fall into the generic except-Exception-as-UNKNOWN path, which used to
    discard the response body and headers entirely -- see this module's
    own docstring, 2026-09-26 second fix, for why this matters."""
    body_text = _read_http_error_body(exc)
    safe_headers = _safe_response_headers(getattr(exc, "headers", None))
    header_str = ", ".join(f"{k}={v}" for k, v in safe_headers.items()) or "(no safe-to-show headers present)"

    parsed = None
    try:
        parsed = json.loads(body_text)
    except Exception:
        parsed = None

    if isinstance(parsed, dict) and "error" in parsed:
        err = parsed.get("error") or {}
        return ProviderResult(
            outcome=OUTCOME_REJECTED, provider_name=provider_name,
            message=(f"Intelliprint returned HTTP {exc.code} {exc.reason} with its own documented "
                     f"error shape -- type={err.get('type', 'unknown')!r} code={err.get('code', 'unknown')!r} "
                     f"message={err.get('message', '')!r} param={err.get('param')!r}. Response headers: {header_str}."),
        )

    # Body did not parse as Intelliprint's documented error JSON -- could be
    # a security/WAF layer, a proxy, or an undocumented response shape.
    # Genuinely ambiguous: do NOT guess REJECTED for something that isn't
    # provably Intelliprint's own app-level response.
    return ProviderResult(
        outcome=OUTCOME_UNKNOWN, provider_name=provider_name,
        message=(f"HTTP {exc.code} {exc.reason} -- body did not match Intelliprint's documented "
                 f"error JSON shape, so this cannot be confirmed as an app-level rejection (it could be "
                 f"a security/WAF layer in front of the API). Response headers: {header_str}. "
                 f"Body (first {_MAX_BODY_CHARS} chars, never includes your API key): {body_text!r}"),
    )


def _map_status(status: str) -> str:
    if status in _ACCEPTED_STATUSES:
        return OUTCOME_ACCEPTED
    if status in _DISPATCHED_STATUSES:
        return OUTCOME_DISPATCHED
    if status in _REJECTED_STATUSES:
        return OUTCOME_REJECTED
    return OUTCOME_UNKNOWN


def _pdf_preview_url(data: dict) -> "str | None":
    """2026-09-26, fourth fix: per Intelliprint's own documented response
    shape (reference/prints/retrieve), each letter in `letters[]` carries
    its own signed `pdf` preview URL (expires after 1 hour) -- this is
    Intelliprint's OWN rendering of the submitted content, not a re-render
    of anything on our side, and is the only reliable way to inspect what
    was actually produced rather than assuming a successful submission
    means correct rendering. Surfaced in ProviderResult.message (free-text
    diagnostic field, not used for outcome logic) so it reaches the
    console output of scripts/intelliprint_test_send.py automatically."""
    letters = data.get("letters") or []
    if letters and isinstance(letters[0], dict) and letters[0].get("pdf"):
        return letters[0]["pdf"]
    return data.get("pdf") or None


def _pages_sheets_note(data: dict) -> str:
    """2026-09-26, fifth fix -- Nick's follow-up correctly pointed out that
    'two PDF pages alone do not establish front-and-back printing': setting
    printing[double_sided]=yes in the request (fourth fix) is a claim about
    what we ASKED for, not evidence of what Intelliprint actually did.
    Re-verified against Intelliprint's current documentation
    (reference/prints/create) for what WOULD constitute that evidence: the
    response carries both `pages` ("total number of pages... may be the
    same as or more than sheets if double-sided printing is used") and
    `sheets` ("physical paper that pages are printed on"), both top-level
    and per-letter. For this letter's 2 HTML pages actually printed duplex
    on one sheet, the correct/expected values are pages=2, sheets=1; if
    duplex did NOT happen, sheets would be 2 (two separate one-sided
    sheets) even though pages is still 2. Surfaced here (not asserted,
    since this codebase cannot inspect Intelliprint's real response from
    this sandbox) so the console output of a real test-mode submission
    shows the actual pages/sheets Intelliprint reports, and Nick can
    confirm duplex by reading it rather than assuming it from the request
    payload alone.

    2026-09-26, seventh fix -- also surfaces the response's own
    `add_address_sheet` field. This field turned out to be RESPONSE-only
    (see send()'s payload comment for the full story of the 400
    parameter_unknown error this caused when sent as a request field) --
    it is Intelliprint's own statement of whether it put the address on a
    separate sheet or the same page as the letter, which is exactly the
    thing Nick's original report ("an apparent separate address sheet,
    making three pages") needs confirmed from the provider's actual
    behaviour, not assumed from anything this adapter sends."""
    letters = data.get("letters") or []
    letter0 = letters[0] if letters and isinstance(letters[0], dict) else {}
    pages = data.get("pages", letter0.get("pages"))
    sheets = data.get("sheets", letter0.get("sheets"))
    add_address_sheet = data.get("add_address_sheet", letter0.get("add_address_sheet"))
    if pages is None and sheets is None:
        note = " (no pages/sheets fields in this response -- cannot confirm duplex from this call.)"
    else:
        duplex_note = ""
        if isinstance(pages, int) and isinstance(sheets, int):
            if sheets < pages:
                duplex_note = " -- sheets < pages, consistent with duplex (front+back on one sheet)."
            elif sheets == pages and pages > 1:
                duplex_note = " -- sheets == pages: this does NOT look duplexed (each page used its own sheet); check printing[double_sided] took effect."
        note = f" pages={pages!r} sheets={sheets!r}.{duplex_note}"
    if add_address_sheet is not None:
        note += f" add_address_sheet={add_address_sheet!r} (Intelliprint's own report of what it did, not something we requested)."
    return note


def _render_html_to_pdf_bytes(html: str) -> bytes:
    """2026-09-26, eighth fix -- see module docstring for the full
    investigation. Renders content_html to an actual PDF using the SAME
    engine and settings already empirically verified elsewhere in this
    codebase (tests/letter_pagination_check/run_pagination_check.py's
    page-count/positioning checks, and every local preview generated for
    Nick) -- Chromium via Playwright, A4, print media, zero margin,
    honouring letter_content.py's @page CSS -- rather than a different PDF
    engine that would need all of that re-verified from scratch. Raises
    RuntimeError on any failure (missing dependency or a real rendering
    error) so send() can turn this into a clean OUTCOME_UNKNOWN with a
    clear message instead of crashing or guessing an outcome.

    THIS IS A NEW PRODUCTION DEPENDENCY (see requirements.txt's own
    comment on this line): file-based submission (this fix) needs
    Playwright AND a downloaded Chromium build wherever this code actually
    sends letters, not just in a test/dev environment."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "playwright is not installed. File-based submission (the fix for "
            "Intelliprint's extra address/cover page -- see this module's docstring, "
            "eighth fix) renders the letter to a PDF before upload, which needs it. "
            "Install with: pip install playwright    then run once: playwright install chromium"
        ) from exc

    import tempfile

    with tempfile.TemporaryDirectory() as tmpdir:
        html_path = os.path.join(tmpdir, "letter.html")
        pdf_path = os.path.join(tmpdir, "letter.pdf")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(html)
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch()
                try:
                    page = browser.new_page()
                    page.goto("file://" + html_path)
                    page.emulate_media(media="print")
                    page.pdf(path=pdf_path, format="A4", print_background=True,
                             margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
                finally:
                    browser.close()
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Rendering the letter to PDF failed ({type(exc).__name__}: {exc}). If this "
                f"is a missing-browser error, run: playwright install chromium"
            ) from exc

        with open(pdf_path, "rb") as f:
            return f.read()


def _cost_pence(data: dict) -> "int | None":
    """The job's confirmed VAT-INCLUSIVE cost in whole pence, or None if the
    response does not carry a valid one.

    Units (Intelliprint docs): "All costs amounts need to be divided by 10^8
    to get the real GBP price", so 1 pence = 10^6 units. `cost.after_tax` is the
    VAT-inclusive figure (verified against the real test job: 90p + 20% VAT =
    108p = 108,000,000 units). Whole units are first normalised (so float noise
    below one unit cannot move the result), then rounded UP to the next whole
    penny -- if a charge ever has a fractional penny, the ledger records the
    larger figure rather than under-recording spend (an exact figure like
    108,000,000 is unaffected).
    Anything that is not a finite positive number (missing, null, string, bool,
    NaN/inf, zero, negative) returns None: callers must treat that as an
    UNRESOLVED cost, never as zero or as the estimate."""
    import math
    cost = data.get("cost") if isinstance(data, dict) else None
    if not isinstance(cost, dict):
        return None
    after_tax = cost.get("after_tax")
    if isinstance(after_tax, bool) or not isinstance(after_tax, (int, float)):
        return None
    if isinstance(after_tax, float) and not math.isfinite(after_tax):
        return None
    units = int(round(after_tax))
    if units <= 0:
        return None
    return -(-units // 1_000_000)


def submission_reference(idempotency_key: str) -> str:
    """The exact `reference` value send() submits for an obligation. ONE
    definition, used by send() and by find_by_reference(), so the recovery
    search can never drift from what was originally submitted."""
    return f"treekey_{idempotency_key[:48]}"


def submitted_mode_marker(test_mode: bool) -> str:
    """Appended to every UNKNOWN send() message so the mode the submission
    was actually made in is recorded with the obligation (letter_obligations.
    last_error). Read back by retention_dispatch_purge to search the SAME
    mode. Absent marker -> recovery refuses to guess and holds for review."""
    return f"[submitted_testmode={'true' if test_mode else 'false'}]"


# Recovery search paging: documented list parameters are limit/skip, with
# response has_more and total_available (reference/prints/list). The limit's
# maximum is not documented, so stay modest; the page cap bounds the work.
_LOOKUP_PAGE_SIZE = 50
_LOOKUP_MAX_PAGES = 10


def _result_from_print(provider_name: str, data: dict, fallback_reference: str) -> ProviderResult:
    """Shared by check_status() and find_by_reference(): turns one Intelliprint
    print object into a ProviderResult (status mapping unchanged)."""
    letters = data.get("letters") or []
    status = (letters[0].get("status") if letters and isinstance(letters[0], dict) else None) or data.get("status")
    pdf_url = _pdf_preview_url(data)
    pdf_note = f" PDF preview (signed URL expires in 1 hour): {pdf_url}" if pdf_url else ""
    pages_sheets_note = _pages_sheets_note(data)
    return ProviderResult(
        outcome=_map_status(status or ""), provider_name=provider_name,
        provider_reference=str(data.get("id", fallback_reference)),
        cost_pence=_cost_pence(data),
        message=f"Reconciled via check_status: status={status!r}.{pdf_note}{pages_sheets_note}",
    )


class IntelliprintProvider(LetterProviderAdapter):
    name = "intelliprint"
    supports_reference_lookup = True
    capabilities = ProviderCapabilities(max_pages=2, countries=("GB",))

    def __init__(self):
        self.api_key = os.getenv("INTELLIPRINT_API_KEY", "").strip()
        self.test_mode = os.getenv("INTELLIPRINT_TEST_MODE", "true").strip().lower() != "false"

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _headers(self) -> dict:
        # 2026-09-26, third fix -- with no User-Agent header set at all,
        # urllib sends its own default (literally "Python-urllib/3.x"), a
        # well-known generic/bot signature that Cloudflare (sitting in
        # front of api.intelliprint.net) can reject outright as
        # browser_signature_banned (Cloudflare error 1010) before the
        # request ever reaches Intelliprint's own application code -- a
        # different failure mode from an application-level 403 (see this
        # module's docstring, second fix). An honest, non-impersonating
        # application identifier avoids that specific class of block --
        # this does not try to look like a browser or bypass any real
        # challenge. Used by send(), check_status(), and
        # scripts/intelliprint_auth_check.py alike, since all three call
        # this same method.
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "User-Agent": "TreeKey/1.0 (+https://treekey.co.uk)",
        }

    def send(self, request: LetterRequest) -> ProviderResult:
        result = self._send_once(request)
        if result.outcome == OUTCOME_UNKNOWN:
            # Record which mode this (ambiguous) submission was made in, so
            # a later reference search can use the same mode (see
            # submitted_mode_marker).
            result = _dc_replace(result, message=f"{result.message} {submitted_mode_marker(self.test_mode)}".strip())
        return result

    def _send_once(self, request: LetterRequest) -> ProviderResult:
        if not self.is_configured():
            # Should never be reached -- the registry checks is_configured()
            # first -- but fail safe (unknown, not accepted) if it is.
            return ProviderResult(outcome=OUTCOME_UNKNOWN, provider_name=self.name,
                                   message="INTELLIPRINT_API_KEY not set.")

        # 2026-09-26, eighth fix -- see module docstring for the full
        # investigation. Render to PDF and submit via `file`, NOT `content`
        # + `recipients`, now that a real submission proved the latter adds
        # Intelliprint's own extra address/barcode page ahead of our
        # letter. Do this BEFORE any network call: a local rendering
        # failure means nothing was sent to Intelliprint at all, so it's
        # reported as UNKNOWN (never accepted/rejected -- nothing happened)
        # without touching the network.
        try:
            pdf_bytes = _render_html_to_pdf_bytes(request.content_html)
        except RuntimeError as exc:
            return ProviderResult(outcome=OUTCOME_UNKNOWN, provider_name=self.name,
                                   message=f"Could not render the letter to PDF, nothing was sent: {exc}")

        payload = {
            "type": "letter",
            # 2026-09-26, eighth fix -- file[content]/file[name] replace the
            # old `content` (raw HTML) field. Per Intelliprint's own
            # "Choose a Content Strategy" docs: submitting HTML `content`
            # requires `recipients` supplied separately, and Intelliprint
            # generates its OWN address application for that content
            # strategy -- confirmed empirically (Nick's real test-mode PDF)
            # to be an extra physical leading page/sheet bearing only the
            # address and a barcode, taken from `recipients[]` data (its
            # text -- "Sample Homeowner" -- matched request.applicant_name
            # exactly, and did not appear anywhere in our submitted HTML),
            # not from anything our HTML/CSS asked for. Submitting a
            # pre-rendered `file` instead is documented as the OTHER
            # content strategy, where "Intelliprint will read the recipient
            # address directly from the file" -- i.e. no separate address
            # application/page, since the design's own page (our
            # .address-clear-zone, already positioned per Intelliprint's
            # official A4 template -- see letter_content.py) already
            # carries it. `file[content]` takes base64-encoded file data
            # per reference/prints/create's documented base64 upload shape
            # (avoids needing a multipart/form-data request -- this stays
            # form-urlencoded like every other field here).
            "file[content]": base64.b64encode(pdf_bytes).decode("ascii"),
            "file[name]": f"treekey_{request.idempotency_key[:40]}.pdf",
            "reference": submission_reference(request.idempotency_key),
            "testmode": "true" if self.test_mode else "false",
            "confirmed": "true",
            # 2026-09-26, fourth fix -- both previously left unset, so
            # Intelliprint's own documented defaults applied silently:
            # printing.double_sided defaults to "no" (simplex), which
            # would print this letter's two HTML pages as two SEPARATE
            # one-sided sheets rather than one sheet printed front and
            # back -- not what "the privacy page must be on the reverse
            # of the introduction" needs. Set explicitly so the intended
            # duplex pairing (page 1 front / page 2 back, one sheet)
            # actually happens rather than relying on an implicit default.
            "printing[double_sided]": "yes",
        }
        # 2026-09-26, eighth fix -- `recipients[...]` deliberately OMITTED.
        # Per reference/prints/create: "If you are providing a file of
        # letters with pre-inserted addresses, you do not need to provide
        # recipients as Intelliprint will automatically extract the
        # addresses from the file." Supplying it anyway was the untested
        # alternative; omitting it matches the exact documented usage this
        # fix relies on, so the next test isolates one variable at a time.
        # KNOWN OPEN QUESTION, not resolved here: whether real (non-test)
        # sends need recipients[] restored for postage-cost calculation or
        # deliverability checks despite this note -- worth confirming with
        # Intelliprint support before live sending is ever enabled; testmode
        # sends (the only kind this codebase can currently trigger) are
        # unaffected either way.
        http_request = Request(
            f"{API_BASE}/prints",
            data=urlencode(payload).encode("utf-8"),
            headers=self._headers(),
            method="POST",
        )
        try:
            with build_opener(_NoRedirects()).open(http_request, timeout=30) as response:
                data = json.loads(response.read(2_000_000).decode())
        except urllib.error.HTTPError as exc:
            # A real HTTP response with a non-2xx status -- Intelliprint's
            # server was reached and answered. Read its body/headers instead
            # of discarding them (this module's docstring, 2026-09-26 second
            # fix): only classify REJECTED if that body is provably
            # Intelliprint's own documented error shape, otherwise UNKNOWN.
            return _classify_http_error(exc, self.name)
        except Exception as exc:
            # Timeout, connection failure, malformed body -- may have
            # happened AFTER Intelliprint accepted the request, or never
            # reached it at all. Never assume rejection or acceptance here.
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
        pdf_url = _pdf_preview_url(data)
        pdf_note = f" PDF preview (Intelliprint's own rendering, signed URL expires in 1 hour): {pdf_url}" if pdf_url else " (no PDF preview URL in this response -- check Intelliprint's dashboard instead.)"
        pages_sheets_note = _pages_sheets_note(data)
        return ProviderResult(
            outcome=outcome, provider_name=self.name,
            provider_reference=str(data.get("id", "")) or None,
            cost_pence=_cost_pence(data),
            message=f"Intelliprint status={status!r} (testmode={self.test_mode}).{pdf_note}{pages_sheets_note}",
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

        return _result_from_print(self.name, data, provider_reference)

    def find_by_reference(self, idempotency_key: str, *, testmode: bool) -> "ReferenceLookup | None":
        """Read-only recovery search (GET /v1/prints?reference=...&testmode=...)
        for jobs submitted under the exact reference send() uses, in the given
        mode. Documented semantics (reference/prints/list): `reference` is an
        exact, case-sensitive filter; paging is limit/skip; the response has
        has_more and total_available; the default (no testmode) lists LIVE
        jobs only, so testmode is always passed explicitly. `reference` is NOT
        unique and there is no idempotency: callers must treat anything other
        than exactly one complete, valid match as unresolved -- and never read
        "no match" as permission to resend.

        complete=True only when every page was read, has_more ended, and the
        number collected equals total_available. Any error, malformed page,
        missing paging fields or an unconfirmed matching job returns
        complete=False with a reason."""
        if not self.is_configured():
            return ReferenceLookup(False, (), "Intelliprint is not configured here (no API key).")
        reference = submission_reference(idempotency_key)
        collected, skip, total = [], 0, None
        for _ in range(_LOOKUP_MAX_PAGES):
            query = urlencode({"reference": reference, "testmode": "true" if testmode else "false",
                               "limit": _LOOKUP_PAGE_SIZE, "skip": skip})
            http_request = Request(f"{API_BASE}/prints?{query}", headers=self._headers(), method="GET")
            try:
                with build_opener(_NoRedirects()).open(http_request, timeout=30) as response:
                    page = json.loads(response.read(2_000_000).decode())
            except Exception as exc:
                return ReferenceLookup(False, (), f"Lookup failed ({type(exc).__name__}); result set not read.")
            if not isinstance(page, dict) or "error" in page or not isinstance(page.get("data"), list) \
                    or not isinstance(page.get("has_more"), bool) or not isinstance(page.get("total_available"), int):
                return ReferenceLookup(False, (), "Lookup response missing the documented list/paging fields.")
            items = page["data"]
            if any(not isinstance(i, dict) for i in items):
                return ReferenceLookup(False, (), "Lookup response contained a non-object item.")
            collected.extend(items)
            skip += len(items)
            total = page["total_available"]
            if not page["has_more"]:
                break
            if not items:
                return ReferenceLookup(False, (), "Lookup reported more results but returned an empty page.")
        else:
            return ReferenceLookup(False, (), f"Lookup still had more results after {_LOOKUP_MAX_PAGES} pages.")
        ids = [i.get("id") for i in collected if i.get("id")]
        if len(ids) != len(set(ids)):
            return ReferenceLookup(False, (), "Paging returned the same job more than once; result set not reliable.")
        if len(collected) != total:
            return ReferenceLookup(False, (), f"Read {len(collected)} job(s) but provider reports {total} available.")

        # Defence in depth: keep only exact-reference jobs of the requested mode.
        matches = []
        for item in collected:
            if item.get("reference") != reference:
                continue
            if "testmode" in item and bool(item.get("testmode")) != testmode:
                continue
            if not item.get("id"):
                return ReferenceLookup(False, (), "A matching job had no id.")
            if item.get("confirmed") is False:
                return ReferenceLookup(False, (), "A matching job exists but is not confirmed; manual review.")
            matches.append(_result_from_print(self.name, item, str(item["id"])))
        return ReferenceLookup(True, tuple(matches), "")
