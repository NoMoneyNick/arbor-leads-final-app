import os
import requests
import urllib.parse
import logging
from dotenv import load_dotenv

load_dotenv()
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
TEST_EMAIL = os.getenv("TEST_EMAIL", "").strip()
PUBLIC_APP_URL = os.getenv("PUBLIC_APP_URL", "").strip().rstrip("/")
ALERT_BATCH_THRESHOLD = 5

def send_resend_email(subject: str, html_body: str):
    """Sends a letter via the Resend courier."""
    if not RESEND_API_KEY or not TEST_EMAIL: return
    try:
        requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {RESEND_API_KEY}", "Content-Type": "application/json"},
            json={"from": "Vector Data Labs <onboarding@resend.dev>", "to": [TEST_EMAIL], "subject": subject, "html": html_body},
            timeout=10
        )
    except Exception as e:
        logging.error(f"[Postman] Email failed: {e}")

def create_whatsapp_link(lead_ref, city, address, summary):
    """Prepares a WhatsApp message for the owner to send."""
    msg = f"🌳 *NEW LEAD*\n📍 *Location:* {address} ({city})\n🆔 *Ref:* {lead_ref}\n📝 *Work:* {summary}\n\nReply YES to claim."
    return f"https://wa.me/?text={urllib.parse.quote(msg)}"

def dispatch_lead_alerts(city, leads):
    """Decides whether to send individual letters or one big summary (Spam Trap)."""
    if not leads: return
    
    if len(leads) > ALERT_BATCH_THRESHOLD:
        lead_list_html = "".join([f"<li><b>{l['addr']}</b>: {l['summary'][:100]}...</li>" for l in leads[:12]])
        body = f"<h2>{city} Super-Scan: {len(leads)} Leads</h2><ul>{lead_list_html}</ul><p><a href='{PUBLIC_APP_URL or '#'}'>Open Dashboard</a></p>"
        send_resend_email(f"🔥 {city} Digest: {len(leads)} New Leads", body)
    else:
        for l in leads:
            wa = create_whatsapp_link(l['ref'], city, l['addr'], l['summary'])
            body = f"<h3>New Lead: {l['addr']}</h3><p>{l['summary']}</p><a href='{wa}'>WhatsApp Forward</a>"
            send_resend_email(f"New Tree Lead: {l['addr']}", body)