import os
import requests
import urllib.parse
import logging
from dotenv import load_dotenv

load_dotenv()
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
TEST_EMAIL     = os.getenv("TEST_EMAIL", "").strip()
PUBLIC_APP_URL = os.getenv("PUBLIC_APP_URL", "").strip().rstrip("/")
ALERT_BATCH_THRESHOLD = 5

SCORE_EMOJI = {"small": "🟡", "medium": "🟠", "large": "🔴"}
SCORE_LABEL = {"small": "Small — £25", "medium": "Medium — £50", "large": "Large — £75"}


def send_resend_email(subject: str, html_body: str):
    """Sends an email alert via the Resend API."""
    if not RESEND_API_KEY or not TEST_EMAIL:
        logging.warning("[Email] RESEND_API_KEY or TEST_EMAIL not set — skipping.")
        return
    try:
        res = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "from": "Vector Data Labs <onboarding@resend.dev>",
                "to": [TEST_EMAIL],
                "subject": subject,
                "html": html_body
            },
            timeout=10
        )
        if res.status_code not in (200, 201):
            logging.error(f"[Email] Resend returned {res.status_code}: {res.text[:200]}")
    except requests.exceptions.Timeout:
        logging.error("[Email] Resend request timed out.")
    except Exception as e:
        logging.error(f"[Email] Unexpected error: {e}")


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
    Batches into a digest if volume exceeds threshold to avoid inbox spam.
    """
    if not leads:
        return

    if len(leads) > ALERT_BATCH_THRESHOLD:
        # Digest email
        rows = "".join([
            f"<tr>"
            f"<td style='padding:6px;'>{SCORE_EMOJI.get(l.get('lead_score','small'), '🟡')}</td>"
            f"<td style='padding:6px;'><b>{l['addr']}</b></td>"
            f"<td style='padding:6px;'>{l['summary'][:80]}...</td>"
            f"<td style='padding:6px; font-weight:bold;'>£{l.get('lead_price', 25)}</td>"
            f"</tr>"
            for l in leads[:15]
        ])
        body = f"""
            <h2>🌳 {city} Lead Digest — {len(leads)} New Leads</h2>
            <table border='1' cellspacing='0' style='border-collapse:collapse; width:100%;'>
                <tr style='background:#f4f4f9;'>
                    <th style='padding:6px;'>Grade</th>
                    <th style='padding:6px;'>Location</th>
                    <th style='padding:6px;'>Description</th>
                    <th style='padding:6px;'>Value</th>
                </tr>
                {rows}
            </table>
            <p><a href='{PUBLIC_APP_URL or "#"}'>Open Dashboard →</a></p>
        """
        send_resend_email(f"🔥 {city} Digest: {len(leads)} New Tree Surgery Leads", body)
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
                <p><a href='{wa}' style='background:#25D366; color:white; padding:10px 20px;
                   border-radius:8px; text-decoration:none;'>📲 Forward on WhatsApp</a></p>
                <p><a href='{PUBLIC_APP_URL or "#"}'>Open Dashboard →</a></p>
            """
            send_resend_email(
                f"{emoji} New {score.title()} Lead: {lead['addr']} (£{price})",
                body
            )