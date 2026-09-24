"""
address_release.py -- 2026-09-18 review, Section 5: "Confirm that the
separate address-release policy gate was implemented, including protection
against alternative routes revealing the address. Do not treat payment,
funding or provider acceptance as legal approval to disclose."

2026-09-22 handoff update: Nick's business model decision (recorded via
Codex, "New purchases never release the homeowner's name or full postal
address to the contractor") supersedes the "flip ADDRESS_RELEASE_LIVE on
once the legal review clears" plan described below for anything NOT
already historically exposed. A NEW allocation cannot be shown the real
address any more, full stop -- not via ADDRESS_RELEASE_LIVE, not via a
recorded per-allocation eligibility decision, not via both together. See
guarded_address_for_lead's own docstring for exactly what changed. The
sections below are kept as the historical record of why the
flag/eligibility machinery exists and how it used to work for a new
allocation -- most of that history is now inert for new leads, but still
governs the (frozen, reported-not-grown) historical-claim population via
is_historical_purchase/count_historical_address_exposure_leads.

WHAT THIS IS NOT:
- It is not `require_lead_ownership` (main.py) / fulfilment.get_lead_owner.
  Ownership answers "did THIS contractor pay for THIS lead" -- a commercial
  question. This module answers a different, independent question: "has a
  human confirmed it is legally OK for TreeKey to show anyone the exact
  street address at all, right now." A contractor can legitimately own a
  lead and still not be shown the address, if this gate is off.
- It is not `funding.py` (has money been reserved/spent) or provider
  acceptance (did a postal provider agree to post a letter). Money moving
  and a provider agreeing to carry mail are commercial/operational facts,
  not a legal basis to disclose someone's home address. Per Nick's explicit
  instruction this section responds to, none of those three facts is ever
  read by this module or should ever substitute for it elsewhere.
- It is not `suppression.py` (postal_suppressions) -- that is a per-address
  opt-out for whether a LETTER gets physically posted TO that address. This
  module is about whether the address is ever rendered into an HTTP
  response TO A CONTRACTOR (or anyone else via the web app). The two are
  orthogonal: an address can be release-blocked here and still get a
  posted letter (the letter goes to the homeowner, not to a viewer of this
  app), and an address can be release-live here while still suppressed
  from posting (a homeowner who objected to receiving mail but whose
  address a paying contractor is still allowed to see, if that's ever the
  policy -- this module doesn't decide that either way, it only gates
  on/off).

WHY IT EXISTS:
docs/launch_checklist.md item 3 (Article 14 disclosure timing) is an
explicitly UNRESOLVED legal question, requiring a UK data-protection
adviser -- not something this session can decide in code. Until that's
resolved, the only safe default is that the literal address of a real
person is not shown to anyone (beyond TreeKey's own admin/ops staff, who
already have a separate, existing trust boundary -- see
require_lead_ownership's "Admin ... always passes" and the various
DASHBOARD_USER/PASS-gated /admin/* routes) through the web app, no matter
who paid for the lead, whether funding was reserved, or whether a postal
provider has confirmed it will carry a letter to it. Ownership/payment
still gates WHO may attempt to view a lead's page at all -- this gate,
checked on top of and after that, decides whether the actual street
address is rendered into the response, or a redacted placeholder is shown
instead.

DEFAULT: OFF (address never released) -- same "fail closed / missing
config fails safely and visibly" posture as fulfilment.letter_sending_live()
(Section 4) and every other gate this session added. Flipping it on is a
deliberate, informed human decision (per the docstring above, expected to
follow the outstanding legal review), not a side effect of a customer
paying, a budget being confirmed, or a provider accepting a job.

USAGE:
Call guarded_address(real_address) at the LAST possible point before an
address string is interpolated into an HTTP response (HTML, a redirect
URL, a WhatsApp share link, an email body rendered for on-screen display,
etc.) -- never earlier, so a route that reads the real address for some
other purpose (e.g. worker.py/letter_providers actually posting a real
physical letter, which must keep using the true address regardless of this
flag -- the letter goes to the homeowner, this flag is about what a
VIEWER of the app sees) never has to route through here at all.

Every known contractor/customer-facing route that renders a specific
lead's exact address is wired to this gate (see docs/launch_checklist.md
and tests/test_address_release_gate.py for the full, current list):
  - /generate-letter/{lead_id}      (letter preview)
  - /generate-street-flyer/{lead_id} (neighbor flyer -- includes the exact
    address in its body text, not just the street name)
  - /street-view/{reference}        (redirect URL itself encodes the
    address -- the single easiest "alternative route" to leak it via a
    shared/bookmarked link)
  - /dashboard                      (contractor_dashboard -- both the
    on-page address text and the WhatsApp-forward link built from it)
  - /my-leads                       (both the paid and free-tier branches)
  - /free-dashboard                 (free-tier claimed-lead address row)
Admin-only views (basic-auth-gated /admin/* routes, cron-secret-gated
scanner endpoints) are a separate, pre-existing trust boundary and are
deliberately NOT redacted by this gate -- TreeKey's own ops staff need the
real address to run the business; the concern this gate addresses is
disclosure to contractors/customers/the public, not to TreeKey itself.

CONTRACTOR-APPROVAL UI -- BUILT, 2026-09-18 review, Section 1 (second
pass): main.py's /letter-settings/preview and /letter-settings/approve
routes now exist. This gate turned out not to interact with them at all,
by construction, for two independent reasons:

  1. The preview a contractor approves is rendered against fixed,
     illustrative SAMPLE lead data (letter_content.PREVIEW_ADDRESS etc,
     via letter_content.render_preview_letter) -- never a real lead's
     address, so this gate's redaction is never in the render path being
     approved at all. contractor_letter_settings is per-contractor, not
     per-lead; there is no specific real lead to redact at setup time.
  2. Even setting that aside, approval no longer fingerprints the full
     rendered HTML (which would have included whatever address text was
     on screen, real or redacted) -- it fingerprints only the
     contractor-controlled fields (business name, phone, notes, template
     version) via letter_content.template_fingerprint, deliberately
     independent of any address. See that function's own docstring for
     why (a full-render fingerprint would make an approval match only the
     ONE lead/address it happened to be computed against, defeating
     "reusable template approval").

The original caution here (kept for the historical record of what this
review flagged and then closed): a future approval UI must compute its
fingerprint from a real server-side render, never from whatever HTML this
gate redacted for on-screen display -- otherwise a contractor approving
while ADDRESS_RELEASE_LIVE is off could fingerprint placeholder text that
worker.promote_pending_approvals's real re-render could never match,
silently stranding every obligation in 'pending_approval' forever. The
route as built sidesteps this entirely (points 1-2 above) rather than
merely avoiding it by convention -- see main.py's own comments on
/letter-settings/approve and tests/test_letter_settings_routes.py's
TestApproveRouteAuthAndFingerprintIntegrity for the tests proving the
fingerprint is always server-computed, from the row actually stored under
the authenticated session's own contractor_email.
"""
import logging
import os
from typing import Optional

# Module-level (not per-call) on purpose -- 2026-09-18 review, Section 2
# (second pass). guarded_address_for_lead_reference/lead_address_release_
# allowed below need to open their own DB connection, and this binds
# "database" ONCE, at address_release.py's own first-import time -- the
# same moment main.py's own `import database` (main.py line ~13) runs,
# since main.py imports this module shortly after (main.py line ~23) in
# the same synchronous import chain. That makes this module's `database`
# reference and main.py's `database` reference the SAME object for the
# rest of the process.
#
# A per-call `import database` (what this used to be) re-resolves
# sys.modules["database"] fresh on every call instead -- which sounds more
# "up to date" but is actually the bug: several test files (e.g.
# test_payments_webhook.py, test_letter_promise_gate.py) deliberately
# replace sys.modules["database"] with a brand-new stub object partway
# through the test run, for their own isolation reasons. `unittest
# discover` imports every test file (running that replacement) before
# running any test, so by the time tests execute, a per-call import here
# would resolve to whichever stub was swapped in LAST -- a different
# object than the one main.database still points to, and a different
# object than whatever @patch("main.database.xxx")/@patch("database.xxx")
# in a given test actually patched. That mismatch silently made every
# route-level "enabled" test in test_address_release_gate.py fall through
# to the fail-safe (redacted/denied) branch instead of exercising the
# eligibility logic under test. Binding once, at import time, avoids the
# whole class of problem -- don't reintroduce a lazy per-call import here.
import database

logger = logging.getLogger("treekey-address-release")

ADDRESS_RELEASE_LIVE_ENV = "ADDRESS_RELEASE_LIVE"
_TRUTHY_ENV_VALUES = ("1", "true", "yes", "on")

# Deliberately generic -- never hints at what the redacted text would have
# said (no partial address, no "on X Street", nothing derived from the
# real value), and never distinguishes "this lead doesn't exist" from
# "the address is being withheld" (same 404-not-403-style caution
# require_lead_ownership already uses, applied here to content instead of
# access).
REDACTED_ADDRESS_PLACEHOLDER = (
    "Address release pending. TreeKey is finalising the legal review "
    "required before exact addresses are shown -- contact support if you "
    "believe this is blocking a job you've already paid for."
)


def address_release_live() -> bool:
    """Read fresh from the environment on every call -- no caching, no
    per-process memoisation -- matching fulfilment.letter_sending_live()'s
    established pattern so flipping this in production takes effect
    immediately without a restart. Default (unset, or any non-truthy
    value): False -- addresses stay redacted."""
    return os.getenv(ADDRESS_RELEASE_LIVE_ENV, "").strip().lower() in _TRUTHY_ENV_VALUES


def guarded_address(real_address) -> str:
    """The single chokepoint every contractor/customer-facing route must
    call immediately before putting a lead's address into a response.
    Returns `real_address` unchanged only when address_release_live() is
    True; otherwise returns the fixed placeholder, regardless of who is
    asking, whether they paid, whether funding was reserved, or whether a
    provider has accepted anything -- none of those are consulted here.

    2026-09-18 review, Section 2: kept as-is for callers with no specific
    lead to key a per-allocation decision against (e.g. letter_content's
    sample preview, which never touches a real lead at all). For any real,
    specific lead, prefer guarded_address_for_lead below -- see its own
    docstring for why a single global flag is no longer the whole
    decision for a real purchased lead."""
    if real_address and address_release_live():
        return real_address
    return REDACTED_ADDRESS_PLACEHOLDER


# ---------------------------------------------------------------------------
# 2026-09-18 review, Section 2: "Review ADDRESS_RELEASE_LIVE. Preserve
# authorised historical purchase access. For new allocations, implement and
# test eligibility at allocation/order level, with the global flag serving
# only as an additional control. Do not invent a legally sufficient release
# trigger; leave that policy configurable and explicitly unresolved."
#
# Before this section, `guarded_address` above was the ONLY decision this
# module made, and it was a single global switch applied identically to
# EVERY lead, regardless of when or how it was claimed. Two problems with
# that as the sole mechanism, once it started actually being flipped for
# real leads: (1) a contractor whose lead was claimed BEFORE this whole
# gate (and the pipeline it belongs to) existed already had that address
# legitimately shown to them -- on their dashboard, in an email already
# sent -- under whatever rules were in force at the time; retroactively
# redacting it the moment ADDRESS_RELEASE_LIVE happens to be off provides
# no protection (the disclosure already happened) and just breaks their
# access to data they already have. (2) Once the global flag IS eventually
# turned on, it would instantly release EVERY lead's address at once, with
# no way to release access more narrowly (e.g. a pilot with one contractor,
# or leads bought under a specific, reviewed sale flow) -- an all-or-
# nothing switch is a blunt instrument for what should be a considered,
# auditable, per-allocation decision.
#
# guarded_address_for_lead below fixes both: a HISTORICAL claim (resolved
# only via letter_dispatches, fulfilment.get_lead_owner's own pre-existing
# "claimed before this module existed" fallback -- see that function's
# docstring) always keeps its address, full stop, regardless of
# ADDRESS_RELEASE_LIVE or anything else. A NEW allocation (has a
# lead_allocations row -- the table this session's fulfilment pipeline
# introduced) needs BOTH an explicit, operator-recorded, per-lead
# eligibility decision (set_allocation_address_eligible -- audited, who/
# when/why) AND the global flag still being on -- the global flag is now
# an ADDITIONAL control layered on top of the per-allocation decision,
# never a substitute for one. Nothing here decides WHAT makes a specific
# allocation eligible or invents a legally sufficient trigger for that
# decision -- set_allocation_address_eligible just records that a named
# human made a call, for auditing; the actual policy for when to call it
# remains entirely with the operator, explicitly unresolved by this code.

ADDRESS_DISCLOSURE_DECISIONS_TABLE = "address_disclosure_decisions"


def init_address_release_schema(cur) -> None:
    cur.execute(f"""
        CREATE TABLE IF NOT EXISTS {ADDRESS_DISCLOSURE_DECISIONS_TABLE} (
            lead_reference TEXT PRIMARY KEY,
            eligible BOOLEAN NOT NULL,
            decided_by TEXT NOT NULL,
            note TEXT,
            decided_at TIMESTAMPTZ DEFAULT NOW()
        );
    """)


def set_allocation_address_eligible(cur, lead_reference: str, *, eligible: bool,
                                     decided_by: str, note: str = "") -> None:
    """Records an explicit, audited, per-allocation disclosure decision.
    This is a MANUAL admin action -- nothing in this codebase calls it
    automatically, and it deliberately does not validate `eligible` against
    any rule (payment, funding, time elapsed, or otherwise). `decided_by`
    and `note` exist so the decision is attributable and explainable later,
    not so this function can second-guess it. See this section's own
    module-level comment for why the actual eligibility POLICY is left
    entirely to the operator."""
    if not decided_by or not decided_by.strip():
        raise ValueError("decided_by is required -- an eligibility decision must be attributable to a named human.")
    cur.execute(f"""
        INSERT INTO {ADDRESS_DISCLOSURE_DECISIONS_TABLE} (lead_reference, eligible, decided_by, note, decided_at)
        VALUES (%s, %s, %s, %s, NOW())
        ON CONFLICT (lead_reference) DO UPDATE SET
            eligible = EXCLUDED.eligible, decided_by = EXCLUDED.decided_by,
            note = EXCLUDED.note, decided_at = NOW();
    """, (lead_reference.strip(), bool(eligible), decided_by.strip(), (note or "").strip()))


def get_allocation_address_eligible(cur, lead_reference: str) -> bool:
    """False (not eligible) when no decision has ever been recorded -- an
    allocation with no explicit decision is never treated as eligible by
    default; see set_allocation_address_eligible's docstring."""
    cur.execute(f"SELECT eligible FROM {ADDRESS_DISCLOSURE_DECISIONS_TABLE} WHERE lead_reference = %s;",
                (lead_reference.strip(),))
    row = cur.fetchone()
    return bool(row and row[0])


def _lead_allocation_kind(cur, lead_reference: str) -> Optional[bool]:
    """True: this lead has a lead_allocations row -- a NEW allocation
    (this session's fulfilment pipeline, whichever dispatch pipeline is
    actually active -- see fulfilment.py's own MIGRATION NOTES). False:
    found only via letter_dispatches -- a HISTORICAL claim, predating the
    table/gate entirely (the exact same distinction fulfilment.
    get_lead_owner already makes for ownership resolution, reused here for
    disclosure eligibility). None: found in neither -- can't be positively
    classified, so callers must fail toward the strict (new-allocation)
    path, never toward historical."""
    cur.execute("SELECT 1 FROM lead_allocations WHERE lead_reference = %s LIMIT 1;", (lead_reference,))
    if cur.fetchone():
        return True
    cur.execute("SELECT 1 FROM letter_dispatches WHERE lead_reference = %s LIMIT 1;", (lead_reference,))
    if cur.fetchone():
        return False
    return None


def is_historical_purchase(cur, lead_reference: str) -> bool:
    """True only when this lead resolves EXCLUSIVELY via the historical
    letter_dispatches fallback -- never for a new lead_allocations row,
    and never for a lead found in neither table (fails toward 'not
    historical', i.e. the stricter path)."""
    return _lead_allocation_kind(cur, lead_reference) is False


def guarded_address_for_lead(cur, lead_reference: str, real_address) -> str:
    """THE combined, per-lead disclosure decision -- see this section's own
    module-level comment for the full reasoning. Checked in this order:

      1. HISTORICAL (is_historical_purchase) -- the real address, always,
         regardless of ADDRESS_RELEASE_LIVE or any recorded eligibility
         decision. Preserves access that was already legitimately granted
         before this gate (or the new allocation pipeline) existed. Its
         size is meant to be visible, not silently grown or shrunk -- see
         count_historical_address_exposure_leads.
      2. Everything else (a NEW allocation, or a lead_reference this
         function cannot classify at all) -- REDACTED_ADDRESS_PLACEHOLDER,
         unconditionally.

    2026-09-22 handoff: "New purchases never release the homeowner's name
    or full postal address to the contractor" -- stated as an absolute,
    not a toggle. Before this, a NEW allocation could still see the real
    address once BOTH an operator recorded eligible=TRUE
    (set_allocation_address_eligible) AND ADDRESS_RELEASE_LIVE was on --
    a real, working "eventually turn this on" path this handoff
    explicitly closes for anything that isn't historical. That path is
    now structurally unreachable here: ADDRESS_RELEASE_LIVE /
    get_allocation_address_eligible are deliberately NOT consulted for a
    new allocation at all, not merely defaulted off, so there is no
    config value or recorded decision that can produce the real address
    for one. The recording machinery itself (set_allocation_address_
    eligible, the address_disclosure_decisions table) is left in place --
    it's an audit trail of operator judgement calls made under the
    now-superseded policy, not something to silently delete -- but
    nothing in this module reads it toward disclosure any more; treat it
    as historical record only unless Nick explicitly asks for it to mean
    something again.

    Requires an open `cur` -- callers with one already open (most routes,
    mid-request) should pass it through rather than opening a second
    connection; see main.py's call sites for the established pattern."""
    if not real_address:
        return REDACTED_ADDRESS_PLACEHOLDER
    if is_historical_purchase(cur, lead_reference):
        return real_address
    return REDACTED_ADDRESS_PLACEHOLDER


def guarded_address_for_lead_reference(lead_reference: str, real_address) -> str:
    """Self-contained convenience wrapper around guarded_address_for_lead:
    opens and closes its own short-lived connection (same established
    pattern as database.get_dispatched_lead_address_for_contractor), for
    the common case of a caller with no cursor already open at the point
    an address is about to be interpolated into a response -- most of
    main.py's rendering call sites (see this module's own docstring for
    the list). Prefer guarded_address_for_lead directly when the caller
    already has an open cursor (e.g. a batch admin script), to avoid a
    redundant connection.

    Fails safe (redacted) on any DB error, including a lead_reference this
    function cannot find at all -- never raises out to the caller, since a
    rendering route failing to show an address should degrade to the
    placeholder, not to a 500."""
    if not real_address:
        return REDACTED_ADDRESS_PLACEHOLDER
    try:
        conn = database.get_db_conn()
        cur = conn.cursor()
        try:
            return guarded_address_for_lead(cur, lead_reference, real_address)
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[AddressRelease] Could not determine allocation-level eligibility for "
                      f"{lead_reference!r} -- failing safe (redacted): {e}")
        return REDACTED_ADDRESS_PLACEHOLDER


def lead_address_release_allowed(lead_reference: str) -> bool:
    """For routes with no safe reduced version to show at all (e.g.
    generate_street_flyer, street_view_redirect -- the whole point of the
    response IS the address) -- the same two-tier decision as
    guarded_address_for_lead (historical: yes; anything else, including a
    new allocation regardless of any recorded eligibility decision or
    ADDRESS_RELEASE_LIVE: no), as a plain yes/no, without needing a real
    address string on hand to test with. Self-contained (opens its own
    connection, same as guarded_address_for_lead_reference). Fails safe
    (False -- not allowed) on any DB error."""
    try:
        conn = database.get_db_conn()
        cur = conn.cursor()
        try:
            return is_historical_purchase(cur, lead_reference)
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[AddressRelease] Could not determine allocation-level eligibility for "
                      f"{lead_reference!r} -- failing safe (denied): {e}")
        return False


def buyer_facing_reference(cur, lead_reference: str) -> str:
    """2026-09-22 handoff: 'give buyers a random internal reference not
    derivable from the council reference' (the raw council/planning
    reference is itself an indirect identifier -- pasted into a council
    portal's own search box, it can reveal the exact application, address,
    and applicant name this whole module exists to keep from a NEW
    allocation's buyer). For a new allocation, returns lead_allocations.id
    instead -- already a random UUID (gen_random_uuid(), see fulfilment.
    init_fulfilment_schema), already assigned to every new allocation, and
    already NOT derived from or convertible back to the council reference
    -- reusing it here needs no new table/column, only this lookup.
    Historical claims keep showing the real council reference, same
    'preserve authorised historical purchase access, report don't silently
    change it' policy as guarded_address_for_lead -- their buyer-facing UI
    already displays it and has for as long as they've owned the lead.
    Falls back to lead_reference itself if no allocation row can be found
    (should not happen for a real new allocation; fails toward showing
    what the caller already had rather than crashing a render)."""
    if is_historical_purchase(cur, lead_reference):
        return lead_reference
    cur.execute("SELECT id FROM lead_allocations WHERE lead_reference = %s ORDER BY created_at DESC LIMIT 1;",
                (lead_reference,))
    row = cur.fetchone()
    return str(row[0]) if row and row[0] else lead_reference


def buyer_facing_reference_standalone(lead_reference: str) -> str:
    """Self-contained convenience wrapper around buyer_facing_reference for
    the routes below that don't already have a cursor open at the point
    they need to build a link/badge/reference display (e.g. free_dashboard,
    which reaches its lead entirely through database.get_lead_by_reference
    and never opens its own connection). Fails toward returning
    lead_reference unchanged on any DB error -- the same 'degrade to
    showing what the caller already had, never crash a render' posture as
    guarded_address_for_lead_reference, though note this specific fallback
    means the RAW reference could still reach the page if the DB call
    itself fails; that's an availability trade-off (a broken lookup here
    must not 500 a dashboard page), not a policy re-opening -- the normal,
    non-error path always returns the non-identifying value."""
    if not lead_reference:
        return lead_reference
    try:
        conn = database.get_db_conn()
        cur = conn.cursor()
        try:
            return buyer_facing_reference(cur, lead_reference)
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[AddressRelease] Could not compute buyer-facing reference for "
                      f"{lead_reference!r} -- falling back to the real reference: {e}")
        return lead_reference


def resolve_buyer_facing_reference(cur, buyer_facing_ref: str) -> str:
    """Reverse of buyer_facing_reference above. A route that takes a
    buyer-facing reference in its URL path (/generate-letter/{ref},
    /generate-street-flyer/{ref}, /street-view/{ref}) needs the real,
    internal lead_reference before any EXISTING ownership/gating logic
    (require_lead_ownership, guarded_address_for_lead_reference,
    database.get_dispatched_lead_address_for_contractor, etc.) runs --
    none of those were changed by this handoff and all of them key on the
    real lead_reference, never the buyer-facing one.

    buyer_facing_reference hands a caller one of two shapes: a
    lead_allocations.id UUID (new allocation) or the real lead_reference
    itself, unchanged (historical claim). This tries the UUID lookup
    first (cast to text so an old/malformed value that isn't a valid UUID
    literal can't raise a DB type error instead of just missing); if no
    lead_allocations row has that id, treats the input as already being a
    real lead_reference -- the historical case, and a safe fallback for
    any caller still passing a raw reference directly (an old bookmarked
    link, an internal admin call, a lead this function can't otherwise
    classify). Fails toward 'use it verbatim', which is exactly what a
    real lead_reference already was before this function existed -- every
    existing downstream check (ownership, address release) still applies
    unchanged after this resolves the value, so passing through an
    unrecognised input is not itself a disclosure.

    Requires an open `cur`, same convention as guarded_address_for_lead."""
    cur.execute("SELECT lead_reference FROM lead_allocations WHERE id::text = %s LIMIT 1;", (buyer_facing_ref,))
    row = cur.fetchone()
    if row and row[0]:
        return str(row[0])
    return buyer_facing_ref


def resolve_buyer_facing_reference_standalone(buyer_facing_ref: str) -> str:
    """Self-contained convenience wrapper around resolve_buyer_facing_reference
    for the routes below that don't already have a cursor open at the
    point they need to resolve their path parameter (e.g. street_view_
    redirect, which calls straight into
    database.get_dispatched_lead_address_for_contractor with no DB
    connection of its own). Fails toward returning the input unchanged on
    any DB error -- same reasoning as resolve_buyer_facing_reference's own
    fallback: an unresolved value is just treated as an (unrecognised)
    real lead_reference by every downstream check, never as an implicit
    grant of anything."""
    if not buyer_facing_ref:
        return buyer_facing_ref
    try:
        conn = database.get_db_conn()
        cur = conn.cursor()
        try:
            return resolve_buyer_facing_reference(cur, buyer_facing_ref)
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[AddressRelease] Could not resolve buyer-facing reference "
                      f"{buyer_facing_ref!r} -- using it verbatim: {e}")
        return buyer_facing_ref


# ---------------------------------------------------------------------------
# 2026-09-23: Request D, Part 1 ("Remove homeowner names ... from every
# new-buyer page, API response, email, preview and download... Check
# descriptions, links, maps and other fields for indirect identification
# too. Preserve the agreed historical-access distinction, but do not treat
# historical disclosures as undone.") The applicant/homeowner NAME is just
# as much an indirect identifier as the exact address -- arguably more
# directly identifying, since it names a specific real person rather than
# a place -- and was never gated at all before this: guarded_address_for_
# lead(_reference) only ever touched the `address` column. This mirrors
# that exact two-tier structure (historical: shown; anything else,
# including a new allocation, unconditionally hidden) rather than
# inventing a new policy for names.

def guarded_applicant_name_for_lead(cur, lead_reference: str, real_applicant_name):
    """Same two-tier decision as guarded_address_for_lead, applied to the
    applicant/homeowner name instead of the address:

      1. HISTORICAL (is_historical_purchase) -- the real name, unchanged,
         if the council published one (councils don't always publish an
         applicant name -- None/empty stays None/empty either way, this
         never invents a name that wasn't there).
      2. Everything else (a NEW allocation, or a lead_reference this
         function cannot classify) -- None, unconditionally. Callers
         render this as "no applicant line at all", not as a placeholder
         string (a placeholder here would itself be a new, invented UI
         element this handoff didn't ask for; simply omitting the line is
         the minimal change that still stops the leak).

    Requires an open `cur`, same convention as guarded_address_for_lead."""
    if not real_applicant_name:
        return None
    if is_historical_purchase(cur, lead_reference):
        return real_applicant_name
    return None


def guarded_applicant_name_for_lead_reference(lead_reference: str, real_applicant_name):
    """Self-contained convenience wrapper around
    guarded_applicant_name_for_lead, same established pattern as
    guarded_address_for_lead_reference -- opens and closes its own
    short-lived connection. Fails safe (hidden -- returns None) on any DB
    error, same reasoning as the address equivalent: a rendering route
    failing to resolve historical status should degrade to NOT showing
    the name, never to showing it by default."""
    if not real_applicant_name:
        return None
    try:
        conn = database.get_db_conn()
        cur = conn.cursor()
        try:
            return guarded_applicant_name_for_lead(cur, lead_reference, real_applicant_name)
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[AddressRelease] Could not determine allocation-level eligibility for "
                      f"{lead_reference!r} (applicant name) -- failing safe (hidden): {e}")
        return None


def guarded_summary_for_lead(cur, lead_reference: str, real_summary):
    """2026-09-23, Request F follow-up (external review finding, "High":
    free-text descriptions remain exposed... HTML escaping alone is not
    anonymisation): the SAME two-tier historical/new decision as
    guarded_address_for_lead, applied to the free-text job `summary`
    instead of the structured `address` column.

    Real scraped council descriptions very often restate the site address
    inline (e.g. "T1 - Ash - Fell. Site: 14 Oak Avenue, Newark, NG22 8AA")
    -- database._redact_address_from_summary already exists for exactly
    this (built Sep 9 2026) and was already applied to every PRE-purchase
    view (the marketplace, the prepurchase lead-detail page, the teaser/
    cold-outreach emails via notifications._redacted_summary). It was
    never applied to any POST-purchase view -- the dashboard, /my-leads,
    /free-dashboard, and both the purchased-lead and free-lead-granted
    emails all rendered the RAW summary, unconditionally, even for a NEW
    allocation whose `address` column is correctly redacted on the very
    same page. That is a real gap, not a theoretical one: showing the raw
    summary next to a redacted address placeholder can still hand a
    contractor the homeowner's exact address, just via the description
    text instead of the address field -- defeating the "new purchases
    never release the homeowner's... address" policy guarded_address_for_
    lead exists to enforce, for exactly the population (a NEW allocation)
    that policy is supposed to protect.

    Decision:
      1. HISTORICAL (is_historical_purchase) -- the real summary,
         unchanged. Matches guarded_address_for_lead's own historical
         carve-out: a historical claim's address is already fully
         disclosed on the same page, so redacting the summary text would
         only hide information the buyer can already see in the address
         line right next to it, achieving nothing.
      2. Everything else (a NEW allocation, or a lead_reference this
         function cannot classify) -- database._redact_address_from_
         summary(real_summary): a best-effort regex scrub of full
         postcodes and "<number> <street> Road/Street/Avenue/..."
         patterns, THE SAME mechanism (not a new one) already relied on
         for every pre-purchase view. This is an explicit, reported
         limitation, not a claimed guarantee: it is regex-based free-text
         redaction, not verified anonymisation -- see database._redact_
         address_from_summary's own docstring ("free-text addresses can't
         be found with 100% certainty"). It measurably narrows the
         address-in-summary leak using the same tool this codebase
         already trusts elsewhere; it does not eliminate the residual
         risk of an address restated in a form the regex doesn't
         recognise (a house name with no number, a landmark reference).
         Reported as a residual, unresolved limitation -- not fixed
         further here, since a stronger guarantee (an allow-listed
         structured description, or suppressing free text outright)
         is a product decision for Nick, not something to invent
         unilaterally on top of the existing mechanism.

    Requires an open `cur`, same convention as guarded_address_for_lead."""
    if not real_summary:
        return real_summary
    if is_historical_purchase(cur, lead_reference):
        return real_summary
    return database._redact_address_from_summary(real_summary)


def guarded_summary_for_lead_reference(lead_reference: str, real_summary):
    """Self-contained convenience wrapper around guarded_summary_for_lead,
    same established pattern as guarded_address_for_lead_reference --
    opens and closes its own short-lived connection. Fails safe on any DB
    error by applying the redaction (treating an unclassifiable reference
    the same as a NEW allocation, never as historical) -- a rendering
    route that can't resolve historical status should degrade toward
    LESS disclosure, matching guarded_address_for_lead_reference's own
    fail-safe direction, not toward showing the raw text by default."""
    if not real_summary:
        return real_summary
    try:
        conn = database.get_db_conn()
        cur = conn.cursor()
        try:
            return guarded_summary_for_lead(cur, lead_reference, real_summary)
        finally:
            cur.close()
            conn.close()
    except Exception as e:
        logger.error(f"[AddressRelease] Could not determine allocation-level eligibility for "
                      f"{lead_reference!r} (summary) -- failing safe (redacted): {e}")
        try:
            return database._redact_address_from_summary(real_summary)
        except Exception:
            return real_summary


def count_historical_address_exposure_leads() -> int:
    """2026-09-22 handoff: 'historical purchase legacy exposure must be
    reported separately, never silently revoked or retroactively changed.'
    This is that reporting primitive -- the count of distinct lead
    references that STILL get the real address released, exclusively
    because they were claimed via the pre-fulfilment-pipeline
    letter_dispatches path (is_historical_purchase). Does not change
    anything; purely for visibility, e.g. on an admin page or in the
    gap-list report handed to Nick. A lead this counts is one where
    guarded_address_for_lead/lead_address_release_allowed will return the
    real address/True -- every other lead in the system, including every
    new allocation, cannot, per that function's own docstring."""
    conn = database.get_db_conn()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT count(DISTINCT ld.lead_reference)
            FROM letter_dispatches ld
            WHERE NOT EXISTS (
                SELECT 1 FROM lead_allocations la WHERE la.lead_reference = ld.lead_reference
            );
        """)
        return cur.fetchone()[0]
    finally:
        cur.close()
        conn.close()
