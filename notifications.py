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

SCORE_EMOJI = {"small": "🟡", "medium": "🟠", "large": "🔴"}
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
        return "<span style='color:#b45309; font-weight:bold;'>⚠️ Agent on record</span>"
    if has_agent is False:
        return "<span style='color:#059669;'>✅ No agent listed</span>"
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


def send_purchased_lead_email(customer_email: str, lead_data: dict):
    """Emails the completely unlocked lead details to the buyer after a successful Stripe payment."""
    if not RESEND_API_KEY:
        logging.warning("[Email] RESEND_API_KEY not set — cannot send purchased lead.")
        return
        
    subject = f"🌳 Unlocked Lead: {lead_data.get('council_source', 'Local')} Tree Surgery"

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
            f'<p style="margin: 10px 0 0 0; color: #b45309;">⚠️ <strong>Heads up:</strong> this application already lists an agent/contractor '
            f'on record ({agent_label}) — the homeowner may already have someone instructed. Worth confirming before you invest time quoting.</p>'
        )
    elif has_agent is False:
        agent_row = '<p style="margin: 10px 0 0 0; color: #059669;">✅ No agent/contractor is listed on record for this application.</p>'
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
            To view this property on Google Maps, click here:
            <a href="https://www.google.com/maps/search/?api=1&query={urllib.parse.quote(lead_data.get('address', ''))}" style="color: #0ea5e9;">View on Map</a>
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

def create_whatsapp_link(lead_ref: str, city: str, address: str, summary: str,
                         lead_score: str = "small", lead_price: int = 25) -> str:
    """Generates a pre-filled WhatsApp message link for a lead."""
    emoji = SCORE_EMOJI.get(lead_score, "🟡")
    msg = (
        f"🌳 *NEW TREE SURGERY LEAD*\n"
        f"📍 *Location:* {address} ({city})\n"
        f"🆔 *Ref:* {lead_ref}\n"
        f"📝 *Work:* {summary[:200]}\n"
        f"{emoji} *Grade:* {SCORE_LABEL.get(lead_score, 'Small — £25')}\n\n"
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
    
    for lead in leads:
        addr = lead.get("addr", "").upper()
        lead_id = lead.get("id") or lead.get("ref") or lead.get("reference")
        extracted_outcodes = [m.group(1) for m in re.finditer(r'\b([A-Z]{1,2}[0-9][A-Z0-9]?)\s*([0-9][A-Z]{2})\b', addr)]
        
        # Find matching subscribers for this lead's geographic area
        matching_subs = []
        for sub in subscribers:
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
                        customer_leads[email].append(ol)
                        overflow_notices[email] = True

    # 3. Dispatch Formatted Emails to Each Contractor
    for email, routed_leads in customer_leads.items():
        is_overflow = overflow_notices.get(email, False)
        
        notice_banner = """
        <div style="background:#f0fdf4; border-left:3px solid #059669; padding:10px; font-size:12px; color:#065f46; margin-bottom:16px;">
            <b>🔒 Single-Sale Guarantee:</b> These leads have been delivered exclusively to you and burned from our public radar.
        </div>
        """
        if is_overflow:
            notice_banner = """
            <div style="background:#eff6ff; border-left:3px solid #3b82f6; padding:10px; font-size:12px; color:#1e40af; margin-bottom:16px;">
                <b>⚡ Priority Overflow Match:</b> Council filings in your immediate sector were quiet today. To protect your subscription value, we have automatically routed you the highest-value unallocated tree applications from adjacent sectors at zero extra cost.
            </div>
            """

        # Contractor Portal Upgrades (Phase 2, part 1): add a forward-to-WhatsApp
        # button per lead when the contractor has opted into it in /settings. This
        # is a click-to-forward convenience link (create_whatsapp_link), not push
        # delivery via WhatsApp's Business API — no such integration exists here.
        wants_whatsapp = customer_prefs.get(email, "email") in ("whatsapp", "both")

        def _wa_button(l):
            if not wants_whatsapp:
                return ""
            wa = create_whatsapp_link(
                l.get("ref", l.get("reference", "")), city, l.get("addr", ""),
                l.get("summary", ""), l.get("lead_score", "small"), l.get("lead_price", 25)
            )
            return f"<a href='{wa}' style='background:#25D366; color:white; padding:4px 8px; border-radius:4px; text-decoration:none; font-size:12px; margin-left:4px;'>📲 WhatsApp</a>"

        rows = "".join([
            f"<tr>"
            f"<td style='padding:8px;'>{SCORE_EMOJI.get(l.get('lead_score','small'), '🌳')}</td>"
            f"<td style='padding:8px;'><b>{l['addr']}</b></td>"
            f"<td style='padding:8px;'>{l['summary'][:90]}...</td>"
            f"<td style='padding:8px; font-size:11px; white-space:nowrap;'>{_agent_status_badge(l)}</td>"
            f"<td style='padding:8px; white-space:nowrap;'>"
            f"<a href='{PUBLIC_APP_URL}/generate-letter/{urllib.parse.quote(l.get('ref', l.get('reference', '')))}' style='background:#044332; color:white; padding:4px 8px; border-radius:4px; text-decoration:none; font-size:12px; margin-right:4px;'>🖨️ Letter</a>"
            f"<a href='{PUBLIC_APP_URL}/generate-street-flyer/{urllib.parse.quote(l.get('ref', l.get('reference', '')))}' style='background:#059669; color:white; padding:4px 8px; border-radius:4px; text-decoration:none; font-size:12px;'>🏘️ Flyer</a>"
            f"{_wa_button(l)}"
            f"</td>"
            f"</tr>"
            for l in routed_leads
        ])
        body = f"""
            <div style="font-family:sans-serif; max-width:640px; margin:auto; color:#0f172a;">
                <h2 style="color:#044332; margin-bottom:4px;">🌳 TreeKey Intelligence — {len(routed_leads)} New Leads</h2>
                <p style="color:#64748b; font-size:14px; margin-top:0;">Here are the latest statutory tree work applications registered for your crew. "Exclusive" means this lead is sent only to you — it does not mean the homeowner hasn't already engaged someone; check the Agent column below.</p>

                {notice_banner}

                <table border='1' cellspacing='0' style='border-collapse:collapse; width:100%; font-size:13px; border-color:#e2e8f0;'>
                    <tr style='background:#f8fafc;'>
                        <th style='padding:8px; text-align:left;'>Type</th>
                        <th style='padding:8px; text-align:left;'>Location</th>
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
                        "subject": f"🌳 {len(routed_leads)} New Exclusive Planning Leads for your Crew",
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
            f"<td style='padding:6px;'>{SCORE_EMOJI.get(l.get('lead_score','small'), '🌳')}</td>"
            f"<td style='padding:6px;'><b>{l['addr']}</b></td>"
            f"<td style='padding:6px;'>{l['summary'][:80]}...</td>"
            f"<td style='padding:6px; font-weight:bold;'>£{l.get('lead_price', 25)}</td>"
            f"</tr>"
            for l in leads[:15]
        ])
        body = f"""
            <h2>📍 {city} Admin Lead Digest — {len(leads)} New Leads</h2>
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
        send_resend_email(f"🛡️ ADMIN: {city} Digest: {len(leads)} New Tree Surgery Leads", body)
    else:
        # Individual emails per lead
        for lead in leads:
            score = lead.get("lead_score", "small")
            price = lead.get("lead_price", 25)
            emoji = SCORE_EMOJI.get(score, "🟡")
            wa = create_whatsapp_link(
                lead["ref"], city, lead["addr"], lead["summary"], score, price
            )
            body = f"""
                <h3>{emoji} New Tree Surgery Lead — {lead['addr']}</h3>
                <p><b>City:</b> {city}</p>
                <p><b>Ref:</b> {lead['ref']}</p>
                <p><b>Description:</b> {lead['summary']}</p>
                <p><b>Grade:</b> {SCORE_LABEL.get(score, 'Small')} &nbsp; <b>Value: £{price}</b></p>
                <p><b>Agent/contractor on record:</b> {_agent_status_badge(lead)}</p>
                <p><a href='{wa}' style='background:#25D366; color:white; padding:10px 20px;
                   border-radius:8px; text-decoration:none;'>📲 Forward on WhatsApp</a></p>
                <p><a href='{PUBLIC_APP_URL or "#"}'>Open Dashboard →</a></p>
            """
            send_resend_email(
                f"{emoji} New {score.title()} Lead: {lead['addr']} (£{price})",
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
    subject = f"🚨🚨 [CRITICAL WARNING] UPGRADE REQUIRED: {api_name.upper()} REACHING 500 CAP ({current_calls}/{cap} USED) 🚨🚨"
    html_body = f"""
    <div style="font-family:'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width:640px; margin:auto; padding:0; border:4px solid #b91c1c; border-radius:14px; overflow:hidden; background:#ffffff; box-shadow:0 10px 25px rgba(185,28,28,0.2);">
        <!-- URGENT HEADER BANNER -->
        <div style="background:#b91c1c; color:#ffffff; padding:24px 20px; text-align:center;">
            <h1 style="margin:0; font-size:22px; font-weight:900; letter-spacing:1px; text-transform:uppercase;">
                🚨 URGENT ACTION REQUIRED 🚨
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
                    📊 PREDICTIVE QUOTA BURN RATE METRICS:
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
                    👉 CLICK HERE TO UPGRADE ACCOUNT NOW ON UKPLANNINGAPI.CO.UK →
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
    """
    cache_key = f"{category}:{title}"
    now_ts = time.time()
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
            <div style="font-size:12px; font-weight:800; color:{theme['bg']}; text-transform:uppercase; margin-bottom:8px;">📊 INCIDENT METRICS & TELEMETRY:</div>
            <table style="width:100%; border-collapse:collapse; font-size:14px;">{rows}</table>
        </div>
        """

    subject = f"🚨🚨 [{severity.upper()}] {category.upper()}: {title.upper()} 🚨🚨"
    html_body = f"""
    <div style="font-family:'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; max-width:640px; margin:auto; padding:0; border:4px solid {theme['border']}; border-radius:14px; overflow:hidden; background:#ffffff; box-shadow:0 10px 25px rgba(0,0,0,0.15);">
        <!-- URGENT HEADER -->
        <div style="background:{theme['bg']}; color:#ffffff; padding:22px 20px; text-align:center;">
            <h1 style="margin:0; font-size:20px; font-weight:900; letter-spacing:1px; text-transform:uppercase;">
                🚨 {category.upper()} ALERT 🚨
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
                <b style="font-size:13px; color:#0f172a; text-transform:uppercase;">💥 SYSTEM IMPACT:</b>
                <p style="font-size:14px; color:#334155; margin:4px 0 0 0; line-height:1.5;">{impact}</p>
            </div>

            <div style="background:#f0fdf4; border:1px solid #bbf7d0; padding:16px; border-radius:8px; margin:20px 0;">
                <b style="font-size:13px; color:#166534; text-transform:uppercase;">🛠️ ACTION REQUIRED NOW:</b>
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