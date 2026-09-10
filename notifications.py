import os
import requests
import urllib.parse
import logging
from typing import Optional, Dict, Any, Tuple, List
from dotenv import load_dotenv


load_dotenv()
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
TEST_EMAIL     = os.getenv("TEST_EMAIL", "").strip()
PUBLIC_APP_URL = os.getenv("PUBLIC_APP_URL", "").strip().rstrip("/")
ALERT_BATCH_THRESHOLD = 5

SCORE_TAG = {"small": "Small", "medium": "Medium", "large": "Large"}
SCORE_LABEL = {"small": "Small — £19", "medium": "Medium — £29", "large": "Large — £49"}


def _agent_status_badge(lead: dict) -> str:
    """Aug 30 2026: has_agent is now captured on some leads (mesh_scrapers.py's
    Idox detail-page scrape, and PlanIt's other_fields) but was never shown to
    contractors anywhere -- every lead was presented identically as "exclusive"
    even when the council record already lists an agent/contractor. has_agent
    is True / False / None ("not checked" or inconclusive) -- None must read as
    unknown, never as "confirmed no agent"."""
    has_agent = lead.get("has_agent")
    if has_agent is True:
        return "<span style='color:#b45309; font-weight:bold;'>Agent on record</span>"
    if has_agent is False:
        return "<span style='color:#059669;'>No agent listed</span>"
    return "<span style='color:#94a3b8;'>— Unconfirmed</span>"


def _record_email_attempt(success: bool, error: Optional[str]):
    """Sep 3 2026: the failsafe-for-the-failsafe problem. Every incident
    alert in this app -- an expired Stripe key, all councils failing, a
    silently dead lead source -- ultimately depends on this one function
    successfully reaching Resend. If RESEND_API_KEY itself goes bad
    (expired, domain verification lapses, account suspended), every one of
    those alerts fails silently and Nick would never know, because the
    very channel meant to tell him is what broke. An email alert cannot
    warn about its own failure to send. The only fix is recording the
    outcome somewhere OUTSIDE the email channel itself -- here, in
    system_state -- so an external, non-email check (/scheduler-heartbeat)
    can surface it. Best-effort: never lets a DB hiccup here affect
    whether send_resend_email itself succeeds/fails for its caller."""
    try:
        import database
        import datetime
        database.set_system_state("last_email_send_at", datetime.datetime.utcnow().isoformat() + "Z")
        database.set_system_state("last_email_send_status", "ok" if success else "failed")
        database.set_system_state("last_email_send_error", (error or "")[:300])
    except Exception:
        pass


def send_resend_email(subject: str, html_body: str) -> bool:
    """Sends an email alert via the Resend API. Returns True only on a
    confirmed successful send -- callers (notably send_system_incident_alert's
    throttle and its own "Sent" log line) previously assumed this always
    succeeded, which meant a real failure (e.g. today's unverified-domain
    403s) was logged as "Sent" and silently started the alert's throttle
    window anyway, delaying the NEXT attempt by hours even though nothing
    had actually gone out."""
    if not RESEND_API_KEY or not TEST_EMAIL:
        logging.warning("[Email] RESEND_API_KEY or TEST_EMAIL not set — skipping.")
        _record_email_attempt(False, "RESEND_API_KEY or TEST_EMAIL not configured")
        return False
    try:
        res = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "from": "TreeKey Intelligence <leads@treekey.uk>",
                "to": [TEST_EMAIL],
                "subject": subject,
                "html": html_body
            },
            timeout=10
        )
        if res.status_code not in (200, 201):
            logging.error(f"[Email] Resend returned {res.status_code}: {res.text[:200]}")
            _record_email_attempt(False, f"HTTP {res.status_code}: {res.text[:200]}")
            return False
        _record_email_attempt(True, None)
        return True
    except requests.exceptions.Timeout:
        logging.error("[Email] Resend request timed out.")
        _record_email_attempt(False, "Resend request timed out")
        return False
    except Exception as e:
        logging.error(f"[Email] Unexpected error: {e}")
        _record_email_attempt(False, str(e)[:300])
        return False


def send_transactional_email(to_email: str, subject: str, html_body: str,
                              from_label: str = "TreeKey Support <leads@treekey.uk>",
                              headers: Optional[Dict[str, str]] = None) -> bool:
    """Sep 5 2026 CRITICAL FIX: send_resend_email() above always sends to the
    fixed internal TEST_EMAIL address -- correct for the admin/incident
    alerts it was built for, but main.py's magic-link login handler
    (request_magic_link) was calling THAT function for the actual
    customer-facing login email, meaning every contractor requesting a
    1-tap login link had it delivered to Nick's own TEST_EMAIL inbox
    instead of their own -- the entire login flow was silently broken for
    every real contractor except whoever's email happens to be TEST_EMAIL.
    Root-caused live while building the free-signup feature (which also
    needs to email a real customer directly). This function is the
    customer-facing counterpart to send_resend_email: same Resend call
    shape as send_purchased_lead_email's already-correct direct request,
    just generalised so future customer-facing sends don't reach for the
    admin-only helper by mistake."""
    if not RESEND_API_KEY:
        logging.warning(f"[Email] RESEND_API_KEY not set — cannot send to {to_email}.")
        _record_email_attempt(False, "RESEND_API_KEY not configured")
        return False
    if not to_email or "@" not in to_email:
        logging.warning(f"[Email] Refusing to send — {to_email!r} doesn't look like an email address.")
        _record_email_attempt(False, "invalid recipient")
        return False
    try:
        payload = {
            "from": from_label,
            "to": [to_email],
            "subject": subject,
            "html": html_body
        }
        if headers:
            # Sep 10 2026: lets callers (e.g. send_free_lead_code_email) attach
            # real RFC-standard email headers -- specifically List-Unsubscribe
            # / List-Unsubscribe-Post, which Gmail/Yahoo treat as a legitimacy
            # signal for tab placement (Primary vs Promotions) and which their
            # bulk-sender rules require one-click support for. Resend's own API
            # docs confirm "headers" is a plain {name: value} object on the
            # send-email payload -- verified live against resend.com/docs
            # rather than assumed, per Nick's no-guessing rule.
            payload["headers"] = headers
        res = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=10
        )
        if res.status_code not in (200, 201):
            logging.error(f"[Email] Failed to send to {to_email}: HTTP {res.status_code}: {res.text[:200]}")
            _record_email_attempt(False, f"HTTP {res.status_code}: {res.text[:200]}")
            return False
        logging.info(f"[Email] Sent '{subject}' to {to_email}")
        _record_email_attempt(True, None)
        return True
    except requests.exceptions.Timeout:
        logging.error(f"[Email] Request to send to {to_email} timed out.")
        _record_email_attempt(False, "Resend request timed out")
        return False
    except Exception as e:
        logging.error(f"[Email] Unexpected error sending to {to_email}: {e}")
        _record_email_attempt(False, str(e)[:300])
        return False


def _street_view_link_html(address: str) -> str:
    """Sep 9 2026, Nick's ask: "send a google street view screenshot of the
    address or at least a google street view link when they buy the lead."
    See database.street_view_url's own docstring for why this is a real
    Street View pano link (not just a map pin) built with zero new cost or
    API key, and when it honestly falls back to a plain Maps search link
    instead. Shared with the dashboard's own "Street View" button (see
    main.py) via that one function, so both stay identical."""
    import database
    url = database.street_view_url(address)
    is_pano = "map_action=pano" in url
    label = "Open Street View" if is_pano else "View on Map"
    verb = "see this property in Google Street View" if is_pano else "view this property on Google Maps"
    # Sep 10 2026, Nick's ask: this is Google's nearest-available imagery to
    # the geocoded address, not a verified photo of the actual property --
    # it can be outdated, or (especially on rural/unnamed roads) show a
    # neighbouring building instead. Said plainly so nobody mistakes it for
    # a guarantee.
    caveat = (
        ' <span style="color:#94a3b8; font-size:12px;">(imagery may be out of date or not show the exact property)</span>'
        if is_pano else ""
    )
    return f'To {verb}, click here: <a href="{url}" style="color: #0ea5e9;">{label}</a>{caveat}'


def send_purchased_lead_email(customer_email: str, lead_data: dict):
    """Emails the completely unlocked lead details to the buyer after a successful Stripe payment."""
    if not RESEND_API_KEY:
        logging.warning("[Email] RESEND_API_KEY not set — cannot send purchased lead.")
        return
        
    subject = f"Unlocked Lead: {lead_data.get('council_source', 'Local')} Tree Surgery"

    # Aug 30 2026: applicant_name/agent_name/agent_company/has_agent are now
    # captured by the scraper (see mesh_scrapers.py) and returned by
    # database.burn_lead_inventory, but this email never surfaced them --
    # the buyer paid for a lead and got an address with no homeowner name
    # and no honest signal of whether a tree surgeon may already be
    # instructed. has_agent can be True / False / None ("not checked" or
    # "checked but inconclusive" -- never treat None as "no agent").
    applicant_name = lead_data.get("applicant_name")
    agent_name = lead_data.get("agent_name")
    agent_company = lead_data.get("agent_company")
    has_agent = lead_data.get("has_agent")

    applicant_row = (
        f'<p style="margin: 0 0 10px 0;"><strong>Homeowner / Applicant:</strong> {applicant_name}</p>'
        if applicant_name else
        '<p style="margin: 0 0 10px 0; color: #94a3b8;"><strong>Homeowner / Applicant:</strong> Not published by the council for this application.</p>'
    )

    if has_agent is True:
        agent_label = agent_company or agent_name or "an agent"
        agent_row = (
            f'<p style="margin: 10px 0 0 0; color: #b45309;"><strong>Heads up:</strong> this application already lists an agent/contractor '
            f'on record ({agent_label}) — the homeowner may already have someone instructed. Worth confirming before you invest time quoting.</p>'
        )
    elif has_agent is False:
        agent_row = '<p style="margin: 10px 0 0 0; color: #059669;">No agent/contractor is listed on record for this application.</p>'
    else:
        agent_row = '<p style="margin: 10px 0 0 0; color: #94a3b8;">Agent/contractor status: not confirmed — the council record didn\'t clearly show one way or the other.</p>'

    html = f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #e5e7eb; border-radius: 8px;">
        <h2 style="color: #059669; margin-top: 0;">Lead Unlocked Successfully!</h2>
        <p style="color: #374151;">Thank you for your purchase. Here are the details for the lead you just secured. This lead has been permanently removed from the marketplace.</p>

        <div style="background: #f8fafc; padding: 15px; border-radius: 6px; margin: 20px 0; border: 1px solid #e2e8f0;">
            <p style="margin: 0 0 10px 0;"><strong>Reference:</strong> {lead_data.get('reference', 'N/A')}</p>
            <p style="margin: 0 0 10px 0;"><strong>Address:</strong> {lead_data.get('address', 'N/A')}</p>
            <p style="margin: 0 0 10px 0;"><strong>Source:</strong> {lead_data.get('council_source', 'N/A')}</p>
            <p style="margin: 0 0 10px 0;"><strong>Estimated Value Grade:</strong> {lead_data.get('lead_score', 'Medium').title()}</p>
            {applicant_row}
            <p style="margin: 0;"><strong>Description / Summary:</strong><br/>
               <span style="color: #475569; font-size: 14px;">{lead_data.get('summary', 'No summary available.')}</span>
            </p>
            {agent_row}
        </div>

        <p style="font-size: 13px; color: #64748b;">
            {_street_view_link_html(lead_data.get('address', ''))}
        </p>
        <p style="font-size: 12px; color: #94a3b8;">
            Note: UK councils do not publish a homeowner's phone number or email address on planning applications. This lead includes everything that is legally published: the address, the applicant name (when the council records it), and the application details above.
        </p>
    </div>
    """
    
    try:
        res = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "from": "TreeKey Support <leads@treekey.uk>",
                "to": [customer_email],
                "subject": subject,
                "html": html
            },
            timeout=10
        )
        if res.status_code not in (200, 201):
            logging.error(f"[Email] Failed to send purchased lead to {customer_email}: {res.text[:200]}")
        else:
            logging.info(f"[Email] Sent purchased lead details to {customer_email}")
    except Exception as e:
        logging.error(f"[Email] Error sending purchased lead to {customer_email}: {e}")


def send_free_lead_granted_email(customer_email: str, lead_data: dict, unsubscribe_url: str = "") -> bool:
    """Sep 10 2026, Nick's ask (verbatim: "once they own a lead we give them
    everything we have on it, everything we can find"). A paid marketplace
    purchase already gets this via send_purchased_lead_email (called from
    payments.handle_stripe_webhook), but the free-lead reserve+code
    redesign never grew an equivalent for a FREE grant -- the old
    instant-grant flow had one (send_free_account_welcome_email, deleted
    earlier this session as dead/unsafe), but nothing safe replaced its
    actual job once the architecture moved to reserve-then-redeem. Right
    now a free-lead customer who successfully redeems their code only ever
    sees the full details on the dashboard page itself -- confirmed live
    (Nick's own test) that no email with the unlocked details goes out at
    all. This mirrors send_purchased_lead_email's content exactly (address,
    applicant/agent info, summary, Street View link, the same "what
    councils do/don't publish" note) since the moment they own a lead the
    same redaction rules that apply pre-purchase no longer apply -- only
    the subject/intro wording differs (no "purchase" framing for something
    that cost no money). Routed through send_transactional_email (real
    List-Unsubscribe headers) rather than send_purchased_lead_email's older
    direct Resend call, matching this session's other email fixes."""
    subject = f"Your free lead is confirmed — {lead_data.get('council_source', 'Local')} tree job unlocked"

    applicant_name = lead_data.get("applicant_name")
    agent_name = lead_data.get("agent_name")
    agent_company = lead_data.get("agent_company")
    has_agent = lead_data.get("has_agent")

    applicant_row = (
        f'<p style="margin: 0 0 10px 0;"><strong>Homeowner / Applicant:</strong> {applicant_name}</p>'
        if applicant_name else
        '<p style="margin: 0 0 10px 0; color: #94a3b8;"><strong>Homeowner / Applicant:</strong> Not published by the council for this application.</p>'
    )

    if has_agent is True:
        agent_label = agent_company or agent_name or "an agent"
        agent_row = (
            f'<p style="margin: 10px 0 0 0; color: #b45309;"><strong>Heads up:</strong> this application already lists an agent/contractor '
            f'on record ({agent_label}) — the homeowner may already have someone instructed. Worth confirming before you invest time quoting.</p>'
        )
    elif has_agent is False:
        agent_row = '<p style="margin: 10px 0 0 0; color: #059669;">No agent/contractor is listed on record for this application.</p>'
    else:
        agent_row = '<p style="margin: 10px 0 0 0; color: #94a3b8;">Agent/contractor status: not confirmed — the council record didn\'t clearly show one way or the other.</p>'

    unsub_html = (
        f'<p style="font-size:10px; color:#9ca3af; margin-top:20px;"><a href="{unsubscribe_url}" style="color:#9ca3af;">Unsubscribe</a></p>'
        if unsubscribe_url else ""
    )

    html = f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #e5e7eb; border-radius: 8px;">
        <h2 style="color: #059669; margin-top: 0;">Your free lead is confirmed!</h2>
        <p style="color: #374151;">This job is genuinely yours now — nobody else can claim it. Here are the full details.</p>

        <div style="background: #f8fafc; padding: 15px; border-radius: 6px; margin: 20px 0; border: 1px solid #e2e8f0;">
            <p style="margin: 0 0 10px 0;"><strong>Reference:</strong> {lead_data.get('reference', 'N/A')}</p>
            <p style="margin: 0 0 10px 0;"><strong>Address:</strong> {lead_data.get('address', 'N/A')}</p>
            <p style="margin: 0 0 10px 0;"><strong>Source:</strong> {lead_data.get('council_source', 'N/A')}</p>
            <p style="margin: 0 0 10px 0;"><strong>Estimated Value Grade:</strong> {lead_data.get('lead_score', 'Medium').title()}</p>
            {applicant_row}
            <p style="margin: 0;"><strong>Description / Summary:</strong><br/>
               <span style="color: #475569; font-size: 14px;">{lead_data.get('summary', 'No summary available.')}</span>
            </p>
            {agent_row}
        </div>

        <p style="font-size: 13px; color: #64748b;">
            {_street_view_link_html(lead_data.get('address', ''))}
        </p>
        <p style="font-size: 12px; color: #94a3b8;">
            Note: UK councils do not publish a homeowner's phone number or email address on planning applications. This lead includes everything that is legally published: the address, the applicant name (when the council records it), and the application details above.
        </p>
        {unsub_html}
    </div>
    """
    return send_transactional_email(to_email=customer_email, subject=subject, html_body=html,
                                     headers=_list_unsubscribe_headers(unsubscribe_url))


def _list_unsubscribe_headers(unsubscribe_url: str) -> Optional[Dict[str, str]]:
    """Sep 10 2026: real List-Unsubscribe / List-Unsubscribe-Post headers
    (RFC 2369 / RFC 8058), not just a link in the body. Verified live
    against Google's bulk-sender guidance and current deliverability
    write-ups (Suped, Resend's own blog) rather than assumed: mailbox
    providers read these as a strong "legitimate, compliant sender" signal
    that feeds into Primary-vs-Promotions placement, on top of being a
    requirement once send volume crosses Gmail/Yahoo's bulk-sender
    threshold. List-Unsubscribe-Post=One-Click is only honest to send
    because /unsubscribe and /unsubscribe-teaser in main.py now both have
    a POST handler that actually honours a bare one-click POST (added
    alongside this) -- advertising one-click support without backing it
    is worse than not advertising it at all."""
    if not unsubscribe_url:
        return None
    return {
        "List-Unsubscribe": f"<{unsubscribe_url}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }


def _redacted_summary(summary: Optional[str]) -> str:
    """Sep 10 2026: real scraped council descriptions very often restate
    the site address inline (e.g. "T1 - Ash - Fell. Site: 14 Oak Avenue,
    Newark, NG22 8AA") -- database._redact_address_from_summary already
    exists to strip that, and was already applied to the public ticker and
    homepage notices (see its own docstring), but no EMAIL path called it:
    the free-lead-code confirmation, the teaser email, and the new cold
    Email 1 were all handing out whatever the raw summary contained,
    unredacted, before a lead is ever paid for or code-redeemed. Found
    while investigating the address leak Nick caught live. Every pre-
    purchase email now routes summary text through this."""
    import database
    return database._redact_address_from_summary(summary) if summary else "No summary available."


def _safe_area_label(address: str) -> str:
    """Sep 10 2026 CRITICAL FIX: the previous _extract_street_level (now
    removed) tried to derive a "street, locality" string by splitting the
    raw scraped address on commas and stripping a leading house number.
    Live-tested and confirmed broken -- Nick's real test send leaked a
    COMPLETE unredacted address (house name, house number, street, town,
    county, full postcode) into a cold-outreach email, because the real
    address string wasn't comma-delimited the way the test fixture used to
    write this function was. That's a product-breaking bug: it hands out
    exactly what buying/redeeming a lead is supposed to gate.

    Fixed by not parsing the raw address at all. Instead this pulls just
    the outcode out of it (the same regex-based extraction already proven
    in _extract_outcodes/classify_leads_by_radius, which finds a postcode
    shape anywhere in the text regardless of delimiters) and resolves that
    outcode to a real place name via database.get_outcode_area_label --
    the exact same mechanism already used for the public ticker and the
    admin lead-simulator, both of which deliberately show area, never
    street/house, pre-purchase. Structurally can't leak a street or house
    number: an outcode alone doesn't encode one. Returns e.g. "SM7,
    Reigate and Banstead" -- real, specific enough that Nick's own test
    reaction was "I recognise Banstead, that's near me", without ever
    touching the raw address text.

    Sep 10 2026 follow-up: get_outcode_area_label resolves at OUTCODE level,
    which is ambiguous when one outcode spans more than one district (e.g.
    TN16 is mostly Sevenoaks, Kent, but a sliver of it is the London borough
    of Bromley) -- confirmed live, a real lead in Brasted, Kent (TN16 1JB)
    got wrongly labelled "TN16, Bromley, London". Now prefers a full-
    postcode lookup (database.get_full_postcode_area_label, unambiguous)
    when the address contains a full postcode, falling back to the
    outcode-only resolution when it doesn't -- same structural guarantee as
    before, still never touches street/house-level text."""
    import database
    full_postcodes = database._extract_full_postcodes(address or "")
    if full_postcodes:
        area = database.get_full_postcode_area_label(full_postcodes[0])
        if area.get("label"):
            return area["label"]
    outcodes = database._extract_outcodes(address or "")
    if not outcodes:
        return "your area"
    area = database.get_outcode_area_label(outcodes[0])
    return area.get("label") or outcodes[0]


def send_cold_email_1(email: str, lead_data: dict, code: str, director_name: str = "",
                       company_name: str = "", unsubscribe_url: str = "") -> bool:
    """Sep 10 2026: the actual first-touch cold-outreach email, built from
    the copy Nick approved after the full cross-LLM ranking exercise --
    COLD_EMAIL_SEQUENCE.md's "Final Verdict" section. Rewritten same day
    after Nick's live test caught two real problems with the first version:

    1. PRODUCT-BREAKING: leaked the full raw address (see _safe_area_label
       above for the fix and root cause). Summary text can also restate an
       address inline (the exact issue database._redact_address_from_summary
       was already built to catch for the marketplace/ticker, per its own
       docstring) -- this now runs every summary through that same function
       before it ever reaches an email, which the marketplace/ticker paths
       already did but no pre-purchase EMAIL path (this one, the free-lead-
       code confirmation, or the teaser email) previously did. Fixed in all
       three here and in the two functions above.
    2. "Public notice, not a directory lead" tested as confusing jargon to
       an actual reader, not the pattern-interrupt hook it read as on paper
       -- dropped. What Nick's own reaction confirmed DOES work: recognising
       a real, specific place name ("I recognise Banstead, that's near me")
       is what signals "this is actually about me", not an unexplained
       phrase. Kept the real specificity, cut the jargon.

    Still short, still signed like a real person wrote it (that mechanism
    tested fine) -- just with a small signature/logo line so it reads as
    from an actual business, per Nick's "competent IT department" feedback,
    without turning into a marketing-template card."""
    director_line = f"{director_name}," if director_name else f"Found this for {company_name},"
    area = _safe_area_label(lead_data.get("address", ""))
    work = _redacted_summary(lead_data.get("summary")).strip().rstrip(".")
    if work == "No summary available.":
        work = "tree work"
    if len(work) > 130:
        work = work[:127].rsplit(" ", 1)[0].rstrip() + "..."
    council = lead_data.get("council_source") or "the council"
    ref = lead_data.get("reference") or "on record"
    link = f"{PUBLIC_APP_URL}/free-account"

    # Subject reads cleaner with just the place name, not "SM7, Reigate and
    # Banstead" -- the outcode's kept in the body copy where the extra
    # precision reads as specificity rather than clutter.
    subject_area = area.split(", ", 1)[1] if ", " in area else area
    subject = f"{subject_area} — tree job filed this week, unclaimed"
    unsub_html = (
        f'<p style="font-size:10px; color:#9ca3af; margin-top:40px; padding-top:10px; '
        f'border-top:1px solid #eee;"><a href="{unsubscribe_url}" style="color:#9ca3af;">Unsubscribe</a></p>'
        if unsubscribe_url else ""
    )
    logo_url = f"{PUBLIC_APP_URL}/static/icon-192.png"
    # Still plain/personal in the body copy (that part tested fine) --
    # only the signature gets a small logo, so it reads as a real person
    # writing on behalf of an actual business, not a marketing template.
    html = f"""
    <div style="font-family: -apple-system, Segoe UI, Arial, sans-serif; max-width: 560px; margin: auto; padding: 8px; color:#1f2937; font-size:15px; line-height:1.6;">
        <p style="margin:0 0 14px 0;">{director_line}</p>
        <p style="margin:0 0 14px 0;">Found a live tree job near {area} that nobody's claimed yet.</p>
        <p style="margin:0 0 14px 0;">{council} logged {work} this week (ref {ref}). No tree surgeon's listed as the agent on it yet.</p>
        <p style="margin:0 0 14px 0;">It's a live, unclaimed job on the council's register — we've already pulled it, verified it and set it aside for you, so you're not the one trawling every council portal yourself. Nobody's contacted the homeowner yet. It's yours, free, no card needed: <a href="{link}" style="color:#059669;">{link}</a></p>
        <p style="margin:0 0 14px 0;">Your code: <strong style="font-family:monospace; letter-spacing:1px;">{code}</strong> — enter it on that page to unlock the full address.</p>
        <p style="margin:0 0 14px 0;">I run TreeKey — we scan every UK council's planning register daily for tree work and pass on jobs like this before most contractors even know they exist.</p>
        <p style="margin:0 0 20px 0;">More in a few days if it's useful. No obligation either way.</p>
        <table role="presentation" cellpadding="0" cellspacing="0" style="border-top:1px solid #e5e7eb; padding-top:12px; width:100%;">
            <tr>
                <td style="width:30px; vertical-align:middle;"><img src="{logo_url}" width="24" height="24" alt="TreeKey" style="display:block; border-radius:5px;"></td>
                <td style="vertical-align:middle; padding-left:8px; font-size:14px; color:#374151;">
                    Nick — TreeKey<br>
                    <a href="https://treekey.uk" style="color:#9ca3af; font-size:12px;">treekey.uk</a>
                </td>
            </tr>
        </table>
        {unsub_html}
    </div>
    """
    return send_transactional_email(to_email=email, subject=subject, html_body=html,
                                     from_label="Nick from TreeKey <leads@treekey.uk>",
                                     headers=_list_unsubscribe_headers(unsubscribe_url))


def _blur_address_to_area(address: str) -> str:
    """Sep 5 2026, free-signup teaser emails: shows the general area (the
    postcode outcode, e.g. "NG22") without the street/house-number detail
    that would let a non-paying recipient act on the lead directly --
    that's the entire point of a teaser. Same outcode regex already used
    for real geo-matching elsewhere in this file/database.py, just for
    redaction instead of distance calculation."""
    import re
    m = re.search(r'\b([A-Z]{1,2}[0-9][A-Z0-9]?)\s*[0-9][A-Z]{2}\b', (address or "").upper())
    if m:
        return f"Somewhere in the {m.group(1)} area — exact address unlocks with a subscription"
    return "Exact address unlocks with a subscription"


    # Sep 10 2026: send_free_account_welcome_email removed. It was the
    # OLD instant-grant welcome email from before the reserve+code redesign
    # -- by design back then it showed the FULL unredacted address and raw
    # summary immediately on signup, no gate at all. That directly
    # contradicts the current product (address only unlocks after a real
    # code redemption -- see send_free_lead_code_email) and nothing live
    # calls it any more (verified: no call site in main.py, only leftover
    # defensive mocks in test_main.py, which already tolerate it not
    # existing). Found while investigating Nick's "is this how we present
    # our leads?" report of a live address leak -- this wasn't the leak
    # that fired (that was send_cold_email_1, fixed above), but it was
    # dead code with the exact same class of bug baked in, left in place
    # it's a live landmine for whoever next wires up a free-signup email
    # without knowing the architecture changed. Deleted rather than fixed
    # in place, since the entire premise (unlock everything at signup) no
    # longer matches how the product works.


def send_free_lead_code_email(email: str, lead_data: dict, code: str, expires_hours: float = 72.0,
                               unsubscribe_url: str = "") -> bool:
    """Sep 10 2026, free-lead-promo redesign: replaces the old instant-grant
    welcome email as the FIRST touch for every free-lead request, whichever
    of the three traffic types (cold-email code click, organic search, or a
    lapsed-code re-request) sent them to /free-account. Shows only the rough
    details (same _blur_address_to_area redaction as the teaser emails,
    NOT the full address) -- the full reveal only happens once the code is
    entered back on site, per Nick's explicit spec: "we email them the rough
    details... enter the code... we reveal the lead to them as if bought."
    Unsubscribe link styled small/low per Nick's 10 Sep 2026 instruction on
    the cold-email sequence, matching that same convention here.

    Sep 10 2026, v2 -- Nick's direct feedback on the first live test:
    (1) Gmail sorted it into Promotions, not Primary -- rewritten subject/
    opening copy to read as a confirmation rather than a marketing pitch
    ("free", "no card, no subscription" sales framing removed), added a
    hidden preheader so Gmail's preview snippet is the confirmation line
    rather than whatever text happened to render first, and this now sends
    real List-Unsubscribe / List-Unsubscribe-Post headers (see
    _list_unsubscribe_headers) -- all verified against current deliverability
    guidance rather than guessed, since none of this is guaranteed to move
    a specific message and Nick should know it's a best-effort fix, not a
    promise. (2) "looks cheap and scammy, needs our design stamp" -- added
    the real TreeKey mark (served from /static/icon-192.png, the same file
    used as the site's own favicon/app icon) as a small header, tightened
    the copy to state facts rather than sell, and removed the "same as if
    you'd bought it" comparison-shopping line. (3) "this was not a cold
    sales email with a code, it was just confirmation code" -- reworded
    throughout as a reservation confirmation, not an offer."""
    subject = f"Your TreeKey confirmation code — {lead_data.get('council_source', 'Local')} job reserved"
    expires_label = f"{int(expires_hours)} hours" if expires_hours < 48 else f"{int(expires_hours / 24)} days"
    unsub_html = (
        f'<p style="font-size:11px; color:#94a3b8; margin-top:36px; padding-top:12px; '
        f'border-top:1px solid #e2e8f0;"><a href="{unsubscribe_url}" style="color:#94a3b8;">Unsubscribe</a></p>'
        if unsubscribe_url else ""
    )
    # Hidden preheader: this is the snippet Gmail/Outlook show next to the
    # subject line. Without it, Gmail grabs the first visible text in the
    # body instead -- previously that was "No card, no subscription...",
    # which reads exactly like a marketing teaser. The &nbsp;+zero-width-
    # joiner padding after it is the standard trick to stop Gmail tacking
    # on extra body text after the preheader in the preview snippet.
    preheader = (
        f"Confirmation code for the job reserved for you near "
        f"{_blur_address_to_area(lead_data.get('address', ''))}."
    )
    preheader_html = (
        f'<div style="display:none; max-height:0; overflow:hidden; mso-hide:all;">{preheader}'
        + "&nbsp;&zwnj;" * 40 +
        '</div>'
    )
    logo_url = f"{PUBLIC_APP_URL}/static/icon-192.png"
    html = f"""
    {preheader_html}
    <div style="font-family: -apple-system, Segoe UI, Roboto, sans-serif; max-width: 600px; margin: auto; padding: 24px; border: 1px solid #e5e7eb; border-radius: 8px;">
        <table role="presentation" cellpadding="0" cellspacing="0" style="margin-bottom:20px; padding-bottom:16px; border-bottom:1px solid #e5e7eb; width:100%;">
            <tr>
                <td style="width:34px; vertical-align:middle;"><img src="{logo_url}" width="28" height="28" alt="TreeKey" style="display:block; border-radius:6px;"></td>
                <td style="vertical-align:middle; padding-left:8px; font-size:15px; font-weight:bold; color:#111827; letter-spacing:0.3px;">TreeKey</td>
            </tr>
        </table>
        <h2 style="color: #111827; margin: 0 0 12px 0; font-size: 19px;">Your job reservation is confirmed</h2>
        <p style="color: #374151; margin: 0 0 16px 0;">We've reserved the job below for you and taken it off the market. Use the confirmation code to view the full details.</p>
        <div style="background: #f8fafc; padding: 15px; border-radius: 6px; margin: 0 0 20px 0; border: 1px solid #e2e8f0;">
            <p style="margin: 0 0 10px 0;"><strong>Location:</strong> {_blur_address_to_area(lead_data.get('address', ''))}</p>
            <p style="margin: 0 0 10px 0;"><strong>Source:</strong> {lead_data.get('council_source', 'N/A')}</p>
            <p style="margin: 0;"><strong>Job details:</strong><br/>
               <span style="color: #475569; font-size: 14px;">{_redacted_summary(lead_data.get('summary'))}</span>
            </p>
        </div>
        <div style="background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 6px; padding: 15px; margin: 0 0 20px 0; text-align: center;">
            <p style="margin: 0 0 6px 0; font-size: 12px; color: #166534; font-weight: bold; letter-spacing: 0.5px;">CONFIRMATION CODE</p>
            <p style="margin: 0; font-size: 26px; font-weight: bold; color: #14532d; letter-spacing: 2px; font-family: monospace;">{code}</p>
        </div>
        <p style="font-size: 13px; color: #64748b; margin: 0 0 8px 0;">
            Enter this code to view the full job details:
            <a href="{PUBLIC_APP_URL}/free-account" style="color:#059669; font-weight:bold;">View my reserved job →</a>
        </p>
        <p style="font-size: 12px; color: #94a3b8; margin: 0;">
            This code is valid for {expires_label}. If it isn't used in time, the job is released and you can request another one near you from the same page.
        </p>
        <p style="font-size:11px; color:#cbd5e1; margin-top:24px;">TreeKey — Vector Data Labs · treekey.uk</p>
        {unsub_html}
    </div>
    """
    return send_transactional_email(to_email=email, subject=subject, html_body=html,
                                     headers=_list_unsubscribe_headers(unsubscribe_url))


def send_teaser_lead_email(email: str, lead_data: dict, unsubscribe_url: str = "") -> bool:
    """Sep 5 2026, Nick's ask verbatim: signed-up-but-not-subscribed
    contractors get "specific leads in their area... without the finer
    details of the address viewable (blurred out or something) but the
    job details viewable and date it was applied (has to be super recent)
    as a sales prompt". Job detail + filed date are real and unredacted;
    only the address is blurred (see _blur_address_to_area)."""
    subject = f"A {lead_data.get('vertical', 'tree')} job just filed near you — still unclaimed"
    registered = lead_data.get("registered_date") or "recently"
    unsub_html = f'<p style="font-size:11px; color:#94a3b8; margin-top:16px;"><a href="{unsubscribe_url}" style="color:#94a3b8;">Unsubscribe from these emails</a></p>' if unsubscribe_url else ""
    html = f"""
    <div style="font-family: sans-serif; max-width: 600px; margin: auto; padding: 20px; border: 1px solid #e5e7eb; border-radius: 8px;">
        <h2 style="color: #059669; margin-top: 0;">Still-unclaimed job near you</h2>
        <div style="background: #f8fafc; padding: 15px; border-radius: 6px; margin: 20px 0; border: 1px solid #e2e8f0;">
            <p style="margin: 0 0 10px 0;"><strong>Location:</strong> {_blur_address_to_area(lead_data.get('address', ''))}</p>
            <p style="margin: 0 0 10px 0;"><strong>Filed:</strong> {registered}</p>
            <p style="margin: 0;"><strong>Job details:</strong><br/>
               <span style="color: #475569; font-size: 14px;">{_redacted_summary(lead_data.get('summary'))}</span>
            </p>
        </div>
        <p style="font-size: 13px; color: #64748b;">
            Subscribe to unlock the exact address and get jobs like this the moment they're filed, not after we've teased it to you:
            <a href="https://treekey.uk/pricing" style="color:#059669; font-weight:bold;">See plans →</a>
        </p>
        {unsub_html}
    </div>
    """
    return send_transactional_email(to_email=email, subject=subject, html_body=html,
                                     headers=_list_unsubscribe_headers(unsubscribe_url))


def send_teaser_email_batch(min_hours_since_last: float = 72.0, unsubscribe_url_builder=None) -> int:
    """Sep 5 2026, Nick's "limbo account" ask: 1-2x/week teaser emails to
    everyone who signed up free but never subscribed. Never burns/claims
    the lead it shows -- this is a tease, not a delivery, so the lead
    stays available for a real subscriber or a single-lead purchase.
    `unsubscribe_url_builder` is a callable(email) -> url, injected from
    main.py (which owns the session-cookie signing secret) rather than
    imported here, to avoid a circular import between this module and
    main.py."""
    import database
    cohort = database.get_limbo_accounts_due_for_teaser(min_hours_since_last=min_hours_since_last)
    sent = 0
    for account in cohort:
        if not account.get("lat") or not account.get("lon"):
            continue
        exclude_refs = [r for r in (account.get("free_lead_ref"), account.get("last_teaser_lead_ref")) if r]
        lead = database.find_nearest_unclaimed_lead(account["lat"], account["lon"], max_miles=25.0, exclude_refs=exclude_refs)
        if not lead:
            continue
        unsubscribe_url = unsubscribe_url_builder(account["email"]) if unsubscribe_url_builder else ""
        if send_teaser_lead_email(account["email"], lead, unsubscribe_url=unsubscribe_url):
            database.mark_teaser_sent(account["email"], lead_ref=lead["reference"])
            sent += 1
    return sent


def create_whatsapp_link(lead_ref: str, city: str, address: str, summary: str,
                         lead_score: str = "small", lead_price: int = 25) -> str:
    """Generates a pre-filled WhatsApp message link for a lead."""
    msg = (
        f"*NEW TREE SURGERY LEAD*\n"
        f"*Location:* {address} ({city})\n"
        f"*Ref:* {lead_ref}\n"
        f"*Work:* {summary[:200]}\n"
        f"*Grade:* {SCORE_LABEL.get(lead_score, 'Small — £25')}\n\n"
        f"Reply YES to claim this lead."
    )
    return f"https://wa.me/?text={urllib.parse.quote(msg)}"


def dispatch_lead_alerts(city: str, leads: list):
    """
    Sends email alerts for new leads.
    1. Routes leads directly to paying customers if the lead falls in their locked outcode.
    2. Sends a master digest to the Admin (TEST_EMAIL).
    """
    if not leads:
        return

    import database
    import re

    # 0. Make sure this month's counters are reset before we check anyone's quota —
    # otherwise a dispatch run that fires before the daily reset job would wrongly
    # skip fully-eligible contractors using last month's exhausted count.
    database.reset_monthly_quotas_if_needed()

    # 1. Fetch active subscribers ordered strictly by Seniority (subscribed_at ASC)
    subscribers = database.get_active_subscribers_by_seniority()
    customer_leads = {}       # {email: [leads]}
    overflow_notices = {}     # {email: bool}
    customer_prefs = {}       # {email: notification_preference} — Contractor Portal Upgrades (Phase 2)
    customer_coords = {}      # {email: (lat, lon)} — Sep 8 2026, so the dispatch email can show "X.X miles away"

    for lead in leads:
        addr = lead.get("addr", "").upper()
        lead_id = lead.get("id") or lead.get("ref") or lead.get("reference")
        lead_size = lead.get("lead_score") or "small"
        extracted_outcodes = [m.group(1) for m in re.finditer(r'\b([A-Z]{1,2}[0-9][A-Z0-9]?)\s*([0-9][A-Z]{2})\b', addr)]

        # Find matching subscribers for this lead's geographic area
        matching_subs = []
        for sub in subscribers:
            # Sep 8 2026, Nick's proximity-system rework: a contractor who set
            # a job-size preference (small/medium/large) at signup should
            # only ever be matched to jobs at that size -- 'all' (the
            # default, and every pre-existing subscriber before this field
            # existed) keeps matching everything exactly as before.
            sub_job_size = (sub.get("job_size_preference") or "all").lower()
            if sub_job_size != "all" and sub_job_size != lead_size:
                continue

            sub_outcode = sub["outcode"].upper()
            sub_lat = sub.get("lat")
            sub_lon = sub.get("lon")
            sub_radius = sub.get("radius") or 15
            matched = False

            # Priority 1: Exact outcode string match (fastest, highest confidence)
            if sub_outcode in extracted_outcodes or re.search(r'\b' + re.escape(sub_outcode) + r'\b', addr):
                matched = True

            # Priority 2: Haversine distance — check each outcode in the address
            if not matched and sub_lat and sub_lon:
                for oc in extracted_outcodes:
                    lead_lat, lead_lon = database.lookup_outcode_centroid(oc)
                    if lead_lat and lead_lon:
                        dist = database.haversine_miles(sub_lat, sub_lon, lead_lat, lead_lon)
                        if dist <= sub_radius:
                            matched = True
                            break

            # Priority 3: Regional prefix fallback (same postcode AREA, e.g.
            # both "NG" -- Nottingham). Sep 3 2026: this used to be
            # oc.startswith(prefix), a one-sided check -- a subscriber
            # locked to area "M" (Manchester) also matched any outcode
            # STARTING WITH "M", including "ME" (Medway) and "MK" (Milton
            # Keynes), both 150+ miles away and nothing to do with
            # Manchester. Found during the "is this actually fair to a
            # paying customer" audit -- a contractor who paid to lock a
            # specific area could be sold, and travel out for, a lead
            # nowhere near them. Fixed by extracting the SAME 1-2 letter
            # area code from the lead's own outcode and requiring an EXACT
            # match against the subscriber's area, not a substring match --
            # the identical fix already applied to scanners.py's regional-
            # labeling bug the same day.
            if not matched:
                prefix_m = re.match(r'^([A-Z]{1,2})', sub_outcode)
                if prefix_m:
                    prefix = prefix_m.group(1)
                    for oc in extracted_outcodes:
                        oc_prefix_m = re.match(r'^([A-Z]{1,2})', oc)
                        if oc_prefix_m and oc_prefix_m.group(1) == prefix:
                            matched = True
                            break

            if matched:
                matching_subs.append(sub)


        # Seniority Allocation Rule:
        # Longest-tenured subscriber gets the lead first, provided they haven't hit their monthly quota
        for sub in matching_subs:
            email = sub["email"]
            sub_id = sub["id"]

            # Quota enforcement — skip if at monthly limit
            if sub.get("delivered", 0) >= sub.get("quota", 5):
                logging.debug(f"[Quota] Skipping {email} — at monthly quota ({sub.get('delivered')}/{sub.get('quota')})")
                continue

            # Atomically burn and record dispatch
            if database.record_lead_dispatch_and_burn(lead_id, sub_id, email, dispatch_type="seniority_standard"):
                if email not in customer_leads:
                    customer_leads[email] = []
                    customer_prefs[email] = sub.get("notification_preference") or "email"
                    customer_coords[email] = (sub.get("lat"), sub.get("lon"))
                customer_leads[email].append(lead)
                break  # Lead burned and dispatched to #1 senior subscriber; do NOT give to anyone else


    # 2. Check for Under-Supplied Junior Subscribers & Dispatch Adjacent Overflows
    for sub in subscribers:
        email = sub["email"]
        sub_id = sub["id"]
        # If this subscriber received 0 leads this run and their monthly deliveries are low
        if email not in customer_leads and sub.get("delivered", 0) < max(3, int(sub.get("quota", 20) * 0.2)):
            overflow_leads = database.get_closest_unallocated_leads(sub["outcode"], limit=2)
            if overflow_leads:
                for ol in overflow_leads:
                    ol_id = ol.get("id") or ol.get("ref")
                    if database.record_lead_dispatch_and_burn(ol_id, sub_id, email, dispatch_type="overflow_compensation"):
                        if email not in customer_leads:
                            customer_leads[email] = []
                            customer_prefs[email] = sub.get("notification_preference") or "email"
                            customer_coords[email] = (sub.get("lat"), sub.get("lon"))
                        customer_leads[email].append(ol)
                        overflow_notices[email] = True

    # 3. Dispatch Formatted Emails to Each Contractor
    for email, routed_leads in customer_leads.items():
        is_overflow = overflow_notices.get(email, False)
        
        notice_banner = """
        <div style="background:#f0fdf4; border-left:3px solid #059669; padding:10px; font-size:12px; color:#065f46; margin-bottom:16px;">
            <b>Single-Sale Guarantee:</b> These leads have been delivered exclusively to you and burned from our public radar.
        </div>
        """
        if is_overflow:
            notice_banner = """
            <div style="background:#eff6ff; border-left:3px solid #3b82f6; padding:10px; font-size:12px; color:#1e40af; margin-bottom:16px;">
                <b>Priority Overflow Match:</b> Council filings in your immediate sector were quiet today. To protect your subscription value, we have automatically routed you the highest-value unallocated tree applications from adjacent sectors at zero extra cost.
            </div>
            """

        # Contractor Portal Upgrades (Phase 2, part 1): add a forward-to-WhatsApp
        # button per lead when the contractor has opted into it in /settings. This
        # is a click-to-forward convenience link (create_whatsapp_link), not push
        # delivery via WhatsApp's Business API — no such integration exists here.
        wants_whatsapp = customer_prefs.get(email, "email") in ("whatsapp", "both")

        # Sep 8 2026, Nick's proximity-system ask ("we have a large job 4.2
        # miles from your location"): reuses database.lead_distance_miles,
        # the same outcode-extraction + centroid + haversine approach
        # already proven elsewhere in this file. sub_lat/sub_lon come from
        # customer_coords, captured at dispatch time above; either can be
        # None (older subscriber pre-dating lat/lon, or an unresolvable
        # postcode), in which case the column just shows "—" rather than a
        # wrong number.
        c_lat, c_lon = customer_coords.get(email, (None, None))

        def _distance_cell(l):
            dist = database.lead_distance_miles(c_lat, c_lon, l.get("addr", ""))
            return f"{dist} mi" if dist is not None else "—"

        def _wa_button(l):
            if not wants_whatsapp:
                return ""
            wa = create_whatsapp_link(
                l.get("ref", l.get("reference", "")), city, l.get("addr", ""),
                l.get("summary", ""), l.get("lead_score", "small"), l.get("lead_price", 25)
            )
            return f"<a href='{wa}' style='background:#25D366; color:white; padding:4px 8px; border-radius:4px; text-decoration:none; font-size:12px; margin-left:4px;'>WhatsApp</a>"

        rows = "".join([
            f"<tr>"
            f"<td style='padding:8px;'>{SCORE_TAG.get(l.get('lead_score','small'), '')}</td>"
            f"<td style='padding:8px;'><b>{l['addr']}</b></td>"
            f"<td style='padding:8px; white-space:nowrap; color:#044332; font-weight:bold;'>{_distance_cell(l)}</td>"
            f"<td style='padding:8px;'>{l['summary'][:90]}...</td>"
            f"<td style='padding:8px; font-size:11px; white-space:nowrap;'>{_agent_status_badge(l)}</td>"
            f"<td style='padding:8px; white-space:nowrap;'>"
            f"<a href='{PUBLIC_APP_URL}/generate-letter/{urllib.parse.quote(l.get('ref', l.get('reference', '')))}' style='background:#044332; color:white; padding:4px 8px; border-radius:4px; text-decoration:none; font-size:12px; margin-right:4px;'>Letter</a>"
            f"<a href='{PUBLIC_APP_URL}/generate-street-flyer/{urllib.parse.quote(l.get('ref', l.get('reference', '')))}' style='background:#059669; color:white; padding:4px 8px; border-radius:4px; text-decoration:none; font-size:12px;'>Flyer</a>"
            f"{_wa_button(l)}"
            f"</td>"
            f"</tr>"
            for l in routed_leads
        ])
        body = f"""
            <div style="font-family:sans-serif; max-width:640px; margin:auto; color:#0f172a;">
                <h2 style="color:#044332; margin-bottom:4px;">TreeKey Intelligence — {len(routed_leads)} New Leads</h2>
                <p style="color:#64748b; font-size:14px; margin-top:0;">Here are the latest statutory tree work applications registered for your crew. "Exclusive" means this lead is sent only to you — it does not mean the homeowner hasn't already engaged someone; check the Agent column below.</p>

                {notice_banner}

                <table border='1' cellspacing='0' style='border-collapse:collapse; width:100%; font-size:13px; border-color:#e2e8f0;'>
                    <tr style='background:#f8fafc;'>
                        <th style='padding:8px; text-align:left;'>Type</th>
                        <th style='padding:8px; text-align:left;'>Location</th>
                        <th style='padding:8px; text-align:left;'>Distance</th>
                        <th style='padding:8px; text-align:left;'>Description</th>
                        <th style='padding:8px; text-align:left;'>Agent</th>
                        <th style='padding:8px; text-align:left;'>Tool</th>
                    </tr>
                    {rows}
                </table>

                <div style="margin-top:24px; padding:16px; background:#f8fafc; border-radius:8px; text-align:center; font-size:13px; color:#64748b;">
                    Have an idea or want a new tool built for your business? 
                    <a href="{PUBLIC_APP_URL}/suggestions" style="color:#044332; font-weight:bold;">Submit a Suggestion →</a>
                </div>
            </div>
        """
        if RESEND_API_KEY:
            try:
                requests.post(
                    "https://api.resend.com/emails",
                    headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
                    json={
                        "from": "TreeKey Intelligence <leads@treekey.uk>",
                        "to": [email],
                        "subject": f"{len(routed_leads)} New Exclusive Planning Leads for your Crew",
                        "html": body
                    }
                )
                logging.info(f"[Seniority Lead Router] Successfully routed {len(routed_leads)} leads to customer {email}")
            except Exception as e:
                logging.error(f"[Seniority Lead Router] Failed to route to {email}: {e}")

    # 2. Master Digest for Admin
    if len(leads) > ALERT_BATCH_THRESHOLD:
        rows = "".join([
            f"<tr>"
            f"<td style='padding:6px;'>{SCORE_TAG.get(l.get('lead_score','small'), '')}</td>"
            f"<td style='padding:6px;'><b>{l['addr']}</b></td>"
            f"<td style='padding:6px;'>{l['summary'][:80]}...</td>"
            f"<td style='padding:6px; font-weight:bold;'>£{l.get('lead_price', 25)}</td>"
            f"</tr>"
            for l in leads[:15]
        ])
        body = f"""
            <h2>{city} Admin Lead Digest — {len(leads)} New Leads</h2>
            <p>Total leads routed to customers this cycle: {sum(len(v) for v in customer_leads.values())}</p>
            <table border='1' cellspacing='0' style='border-collapse:collapse; width:100%;'>
                <tr style='background:#f4f4f9;'>
                    <th style='padding:6px;'>Grade</th>
                    <th style='padding:6px;'>Location</th>
                    <th style='padding:6px;'>Description</th>
                    <th style='padding:6px;'>Value</th>
                </tr>
                {rows}
            </table>
        """
        send_resend_email(f"ADMIN: {city} Digest: {len(leads)} New Tree Surgery Leads", body)
    else:
        # Individual emails per lead
        for lead in leads:
            score = lead.get("lead_score", "small")
            price = lead.get("lead_price", 25)
            wa = create_whatsapp_link(
                lead["ref"], city, lead["addr"], lead["summary"], score, price
            )
            body = f"""
                <h3>New Tree Surgery Lead — {lead['addr']}</h3>
                <p><b>City:</b> {city}</p>
                <p><b>Ref:</b> {lead['ref']}</p>
                <p><b>Description:</b> {lead['summary']}</p>
                <p><b>Grade:</b> {SCORE_LABEL.get(score, 'Small')} &nbsp; <b>Value: £{price}</b></p>
                <p><b>Agent/contractor on record:</b> {_agent_status_badge(lead)}</p>
                <p><a href='{wa}' style='background:#25D366; color:white; padding:10px 20px;
                   border-radius:8px; text-decoration:none;'>Forward on WhatsApp</a></p>
                <p><a href='{PUBLIC_APP_URL or "#"}'>Open Dashboard →</a></p>
            """
            send_resend_email(
                f"New {score.title()} Lead: {lead['addr']} (£{price})",
                body
            )


def send_api_quota_warning_email(
    api_name: str = "UK PLANNING DATA API",
    current_calls: int = 400,
    cap: int = 500,
    projected_monthly: int = 650,
    reason: str = "Pace calculation projects breach before end of month"
):
    """
    Dispatches an ultra-bold, high-visibility warning email in ALL CAPS
    when API usage pace is calculated to breach the 500-request limit.
    """
    pct = round((current_calls / max(cap, 1)) * 100, 1)
    subject = f"[CRITICAL WARNING] UPGRADE REQUIRED: {api_name.upper()} REACHING 500 CAP ({current_calls}/{cap} USED) "
    html_body = f"""
    <div style="font-family:'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width:640px; margin:auto; padding:0; border:4px solid #b91c1c; border-radius:14px; overflow:hidden; background:#ffffff; box-shadow:0 10px 25px rgba(185,28,28,0.2);">
        <!-- URGENT HEADER BANNER -->
        <div style="background:#b91c1c; color:#ffffff; padding:24px 20px; text-align:center;">
            <h1 style="margin:0; font-size:22px; font-weight:900; letter-spacing:1px; text-transform:uppercase;">
                URGENT ACTION REQUIRED 
            </h1>
            <p style="margin:6px 0 0 0; font-size:14px; font-weight:700; opacity:0.95; text-transform:uppercase;">
                NATIONAL PLANNING DATA API REACHING MONTHLY 500 LIMIT
            </p>
        </div>

        <div style="padding:28px 24px;">
            <p style="font-size:16px; font-weight:800; color:#0f172a; line-height:1.5; margin-top:0;">
                ATTENTION OPERATOR: YOUR LIVE PLANNING DATA SCRAPER IS AT RISK OF PAUSING DUE TO FREE TIER QUOTA LIMITS.
            </p>

            <div style="background:#fef2f2; border:2px solid #f87171; border-radius:10px; padding:18px; margin:20px 0;">
                <div style="font-size:13px; font-weight:800; color:#991b1b; text-transform:uppercase; margin-bottom:8px;">
                    PREDICTIVE QUOTA BURN RATE METRICS:
                </div>
                <table style="width:100%; border-collapse:collapse; font-size:14px;">
                    <tr>
                        <td style="padding:6px 0; color:#475569; font-weight:600;">CURRENT REQUESTS USED:</td>
                        <td style="padding:6px 0; font-weight:900; color:#b91c1c; text-align:right;"><b>{current_calls} / {cap} ({pct}%)</b></td>
                    </tr>
                    <tr>
                        <td style="padding:6px 0; color:#475569; font-weight:600;">PROJECTED MONTH-END TOTAL:</td>
                        <td style="padding:6px 0; font-weight:900; color:#c2410c; text-align:right;"><b>~{projected_monthly} REQUESTS</b></td>
                    </tr>
                    <tr>
                        <td style="padding:6px 0; color:#475569; font-weight:600;">EARLY WARNING TRIGGER:</td>
                        <td style="padding:6px 0; font-weight:800; color:#0f172a; text-align:right;">{reason}</td>
                    </tr>
                </table>

                <div style="margin-top:14px;">
                    <div style="background:#e2e8f0; border-radius:10px; height:20px; width:100%; overflow:hidden;">
                        <div style="background:#dc2626; width:{min(pct, 100)}%; height:100%; border-radius:10px;"></div>
                    </div>
                </div>
            </div>

            <p style="font-size:15px; color:#334155; line-height:1.6;">
                <b>WHAT HAPPENS IF THE CAP IS BREACHED:</b> Once 500 requests are exhausted, the UK Planning API will reject incoming council scans with <code style="background:#fee2e2; color:#991b1b; padding:2px 6px; border-radius:4px; font-weight:bold;">429 Too Many Requests</code>, halting new statutory tree notices until the next billing cycle.
            </p>

            <!-- BIG BOLD CTA BUTTON -->
            <div style="text-align:center; margin:30px 0 20px 0;">
                <a href="https://ukplanningapi.co.uk" target="_blank" 
                   style="display:inline-block; background:#dc2626; color:#ffffff; font-size:16px; font-weight:900; text-transform:uppercase; letter-spacing:0.5px; padding:16px 32px; border-radius:8px; text-decoration:none; box-shadow:0 4px 14px rgba(220,38,38,0.4);">
                    CLICK HERE TO UPGRADE ACCOUNT NOW ON UKPLANNINGAPI.CO.UK →
                </a>
            </div>

            <p style="font-size:13px; text-align:center; color:#64748b; margin-bottom:0;">
                Upgrading takes 60 seconds and ensures uninterrupted 24/7 planning lead monitoring across all 309 English councils, Scotland, and Wales.
            </p>
        </div>

        <div style="background:#f8fafc; border-top:1px solid #e2e8f0; padding:14px 20px; text-align:center; font-size:12px; color:#94a3b8; font-weight:700;">
            VECTOR DATA LABS AUTOMATED RESILIENCE MONITOR • NOTICE DISPATCHED IMMEDIATELY
        </div>
    </div>
    """
    if send_resend_email(subject, html_body):
        logging.warning(f"[URGENT QUOTA WARNING] Dispatched ALL-CAPS alert for {api_name} ({current_calls}/{cap}, projected {projected_monthly}) to {TEST_EMAIL}")
    else:
        logging.error(f"[URGENT QUOTA WARNING] FAILED to dispatch alert for {api_name} ({current_calls}/{cap}) — email did not go out.")


import time
_ALERT_THROTTLE_CACHE = {}

def send_system_incident_alert(
    category: str,
    title: str,
    description: str,
    impact: str,
    action_required: str,
    metric_details: dict = None,
    severity: str = "CRITICAL",
    throttle_hours: float = 4.0
):
    """
    Unified high-visibility system incident alert dispatcher.
    Sends ultra-bold ALL-CAPS emails for any critical issue or near-term system risk.

    Sep 4 2026 (Nick, verbatim): "if the warning emails are mostly ignored i
    will never know when to pay attention ... make sure i only get emails
    when there is a serious issue or a serious issue about to occur." Root
    cause: this function used to email for EVERY severity identically --
    severity only ever changed the HTML colour theme, never whether an
    email actually went out. That's exactly why the Sep 3/4 redeploy
    (which resets net_utils._TLS_ALERT_THROTTLE and IdoxScraper.
    _structure_alert_throttle -- both plain in-process-memory dicts) caused
    ~15 unrelated WARNING alerts to fire in one simultaneous burst, all
    confirmed false alarms by live spot-check (Royal Greenwich/Brent still
    parse fine, Croydon loads fine in a real browser -- see net_utils.py
    and mesh_scrapers.py's own alert functions for the detail).

    New behaviour: CRITICAL and SECURITY are unchanged -- always an
    immediate email, still deduped by the throttle below. WARNING NEVER
    emails individually at all any more -- it's only logged to the
    system_warnings table (database.log_system_warning).

    Sep 8 2026 (Nick, verbatim: "everyday i am getting emails ... this is
    extremely serious and needs a permanent fix not a temporary [one]"):
    the previous version of this got the "don't email every WARNING"
    principle right but the wrong unit of consolidation -- it stopped
    emailing on every OCCURRENCE, but still emailed individually per
    (category, title), i.e. per council/domain. The morning ~20 different
    council portals all crossed the 3-of-7-days threshold within the same
    hour, that was 20 separate emails, each one individually "correctly"
    throttled to 48h but with no throttle shared across THEM. Emailing
    WARNINGs is now handled entirely by send_daily_warning_digest() below
    (wired into the once-daily autonomous cycle in main.py) -- ONE email,
    at most once a day, listing everything currently recurring, or no
    email at all on a quiet day. This function's job for a WARNING is now
    only to log; database.get_all_recurring_warnings() is what the digest
    reads to decide what's actually worth Nick's attention.
    """
    cache_key = f"{category}:{title}"
    now_ts = time.time()

    if severity.upper() == "WARNING":
        try:
            import database
            database.log_system_warning(category, title, description)
        except Exception as e:
            logging.error(f"[SYSTEM WARNING] Failed to log warning {cache_key}: {e}")
        logging.info(f"[SYSTEM WARNING LOGGED] {cache_key} logged (see send_daily_warning_digest for emailing).")
        return

    last_sent = _ALERT_THROTTLE_CACHE.get(cache_key, 0)
    if (now_ts - last_sent) < (throttle_hours * 3600):
        logging.info(f"[ALERT THROTTLED] Suppressed duplicate alert for {cache_key} (sent {round((now_ts - last_sent)/60)}m ago).")
        return

    # Aug 30 2026: the throttle window used to start here, before the send
    # below was even attempted -- a failed send (Resend down, unverified
    # domain, etc.) still "used up" the throttle, silently blocking the next
    # real attempt for up to throttle_hours even though nothing had gone out
    # yet. Now only set once send_resend_email confirms success, further
    # down.

    color_map = {
        "CRITICAL": {"border": "#b91c1c", "bg": "#b91c1c", "card_bg": "#fef2f2", "card_border": "#f87171", "btn": "#dc2626"},
        "WARNING":  {"border": "#ea580c", "bg": "#c2410c", "card_bg": "#fffaf0", "card_border": "#fed7aa", "btn": "#ea580c"},
        "SECURITY": {"border": "#7c2d12", "bg": "#7c2d12", "card_bg": "#fdf4ff", "card_border": "#f0abfc", "btn": "#9333ea"}
    }
    theme = color_map.get(severity.upper(), color_map["CRITICAL"])

    metrics_html = ""
    if metric_details:
        rows = "".join([
            f"<tr><td style='padding:5px 0; color:#475569; font-weight:700;'>{k.upper()}:</td><td style='padding:5px 0; font-weight:900; color:#0f172a; text-align:right;'>{v}</td></tr>"
            for k, v in metric_details.items()
        ])
        metrics_html = f"""
        <div style="background:{theme['card_bg']}; border:2px solid {theme['card_border']}; border-radius:10px; padding:16px; margin:18px 0;">
            <div style="font-size:12px; font-weight:800; color:{theme['bg']}; text-transform:uppercase; margin-bottom:8px;">INCIDENT METRICS & TELEMETRY:</div>
            <table style="width:100%; border-collapse:collapse; font-size:14px;">{rows}</table>
        </div>
        """

    subject = f"[{severity.upper()}] {category.upper()}: {title.upper()} "
    html_body = f"""
    <div style="font-family:'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width:640px; margin:auto; padding:0; border:4px solid {theme['border']}; border-radius:14px; overflow:hidden; background:#ffffff; box-shadow:0 10px 25px rgba(0,0,0,0.15);">
        <!-- URGENT HEADER -->
        <div style="background:{theme['bg']}; color:#ffffff; padding:22px 20px; text-align:center;">
            <h1 style="margin:0; font-size:20px; font-weight:900; letter-spacing:1px; text-transform:uppercase;">
                {category.upper()} ALERT 
            </h1>
            <p style="margin:6px 0 0 0; font-size:14px; font-weight:700; text-transform:uppercase; opacity:0.95;">
                {title.upper()}
            </p>
        </div>

        <div style="padding:26px 22px;">
            <p style="font-size:15px; font-weight:800; color:#0f172a; line-height:1.5; margin-top:0;">
                {description}
            </p>

            {metrics_html}

            <div style="background:#f8fafc; border-left:4px solid {theme['border']}; padding:14px 16px; margin:18px 0; border-radius:4px;">
                <b style="font-size:13px; color:#0f172a; text-transform:uppercase;">SYSTEM IMPACT:</b>
                <p style="font-size:14px; color:#334155; margin:4px 0 0 0; line-height:1.5;">{impact}</p>
            </div>

            <div style="background:#f0fdf4; border:1px solid #bbf7d0; padding:16px; border-radius:8px; margin:20px 0;">
                <b style="font-size:13px; color:#166534; text-transform:uppercase;">ACTION REQUIRED NOW:</b>
                <p style="font-size:14px; color:#14532d; font-weight:700; margin:6px 0 0 0; line-height:1.5;">{action_required}</p>
            </div>

            <p style="font-size:12px; color:#94a3b8; text-align:center; margin-bottom:0;">
                Vector Data Labs Automated Resilience Sentry • Host: Render Production
            </p>
        </div>
    </div>
    """
    sent_ok = send_resend_email(subject, html_body)
    if sent_ok:
        _ALERT_THROTTLE_CACHE[cache_key] = now_ts
        logging.warning(f"[SYSTEM INCIDENT ALERT] Sent {severity} email for {category}: {title} to {TEST_EMAIL}")
    else:
        logging.error(f"[SYSTEM INCIDENT ALERT] FAILED to send {severity} email for {category}: {title} — not throttling, will retry on next occurrence.")


# Category-specific plain-English context for the digest below -- so each
# entry says what kind of problem it actually is, not just its raw title.
_WARNING_CATEGORY_CONTEXT = {
    "SCRAPER TLS FALLBACK": (
        "Not actionable, not costing you leads — the scrape still succeeded, "
        "it just had to skip certificate verification for this portal (their "
        "cert is expired/self-signed/misconfigured, not something we control). "
        "Shown here for visibility only; expect this to keep recurring for the "
        "same handful of portals indefinitely unless the council fixes their cert."
    ),
    "SCRAPER PAGE STRUCTURE": (
        "Worth checking — this usually means a council changed their portal's "
        "page layout and we may be silently missing leads from them until the "
        "parser is updated."
    ),
    "SILENT SOURCE FAILURE": (
        "Worth checking — this source normally produces leads and has gone to "
        "zero with no error raised, which is the exact failure shape the Leeds "
        "ArcGIS bug had before it was found."
    ),
    "GEOCODING API FAILURE": (
        "Worth checking — a postcode/outcode distance lookup (via the free "
        "postcodes.io API) is erroring rather than just returning 'not found'. "
        "While this recurs, radius-based lead matching for affected customers "
        "silently falls back to exact-outcode/regional matches only, which can "
        "mean fewer leads reaching them than they should get."
    ),
}
_DIGEST_THROTTLE_HOURS = 20.0
_last_digest_sent_ts = 0.0


def send_daily_warning_digest() -> bool:
    """Sep 8 2026, Nick's ask (verbatim: "everyday i am getting emails ...
    this is extremely serious and needs a permanent fix not a temporary
    [one] ... build fail safe systems that put the whole thing back on
    track automatically"): the single daily email that replaced individual
    per-council WARNING escalation emails. Reads database.
    get_all_recurring_warnings() (every category+title that's recurred 3+
    of the last 7 days AND fired within the last 24h) and sends ONE email
    listing all of them, grouped by category with plain-English context on
    what each category actually means (see _WARNING_CATEGORY_CONTEXT) --
    instead of a dozen+ separate "RECURRING" emails hitting his inbox
    within the same hour. Sends nothing at all on a quiet day (empty
    list). Called once per autonomous daily cycle (main.py's
    _check_for_silent_source_failures neighbour) -- not a new cron entry
    Nick has to remember to set up, it rides the existing once-a-day
    cycle. Throttled to 20h independently of that cycle's own timing as a
    safety net against ever double-sending in one day."""
    global _last_digest_sent_ts
    now_ts = time.time()
    if (now_ts - _last_digest_sent_ts) < (_DIGEST_THROTTLE_HOURS * 3600):
        logging.info("[WARNING DIGEST] Skipped — already sent within the last %.0fh.", _DIGEST_THROTTLE_HOURS)
        return False

    try:
        import database
        recurring = database.get_all_recurring_warnings(window_days=7, min_days=3, active_within_hours=24)
    except Exception as e:
        logging.error(f"[WARNING DIGEST] Failed to fetch recurring warnings: {e}")
        return False

    if not recurring:
        logging.info("[WARNING DIGEST] Nothing currently recurring — no email sent.")
        return False

    by_category = {}
    for w in recurring:
        by_category.setdefault(w["category"], []).append(w)

    sections_html = ""
    for category, items in sorted(by_category.items()):
        context = _WARNING_CATEGORY_CONTEXT.get(category, "")
        rows = "".join([
            f"<tr><td style='padding:6px 0; color:#0f172a; font-weight:700;'>{w['title']}</td>"
            f"<td style='padding:6px 0; text-align:right; color:#64748b; white-space:nowrap;'>{w['days_seen']}/7 days</td></tr>"
            for w in sorted(items, key=lambda w: -w["days_seen"])
        ])
        sections_html += f"""
        <div style="margin-bottom:22px;">
            <div style="font-size:13px; font-weight:800; color:#0f172a; text-transform:uppercase; margin-bottom:4px;">{category} ({len(items)})</div>
            {f'<p style="font-size:12px; color:#64748b; margin:0 0 8px 0;">{context}</p>' if context else ''}
            <table style="width:100%; border-collapse:collapse; font-size:13px; border-top:1px solid #e2e8f0;">{rows}</table>
        </div>
        """

    total = len(recurring)
    subject = f"Daily warning digest — {total} recurring issue{'s' if total != 1 else ''}"
    html_body = f"""
    <div style="font-family:'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width:640px; margin:auto; padding:0; border:2px solid #cbd5e1; border-radius:14px; overflow:hidden; background:#ffffff;">
        <div style="background:#0f172a; color:#ffffff; padding:20px; text-align:center;">
            <h1 style="margin:0; font-size:18px; font-weight:800;">Daily Warning Digest</h1>
            <p style="margin:6px 0 0 0; font-size:13px; opacity:0.85;">{total} issue{'s' if total != 1 else ''} currently recurring across the last 7 days — one email, not one per issue.</p>
        </div>
        <div style="padding:24px 22px;">
            {sections_html}
            <p style="font-size:12px; color:#94a3b8; text-align:center; margin-top:8px;">
                Vector Data Labs Automated Resilience Sentry • Host: Render Production
            </p>
        </div>
    </div>
    """
    sent_ok = send_resend_email(subject, html_body)
    if sent_ok:
        _last_digest_sent_ts = now_ts
        logging.warning(f"[WARNING DIGEST] Sent digest covering {total} recurring issue(s) to {TEST_EMAIL}.")
    else:
        logging.error("[WARNING DIGEST] FAILED to send — not throttling, will retry on next cycle.")
    return sent_ok