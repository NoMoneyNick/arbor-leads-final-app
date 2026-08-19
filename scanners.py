import os
import requests
import time
import database
import notifications

GLA_API_KEY = os.getenv("GLA_API_KEY", "").strip()
TREE_GOLD = ["tree", "arbor", "felling", "stump", "surgery", "crown", "tpo"]

def scan_london_leads():
    """Scout goes to the London Datahub to find tree surgery jobs."""
    headers = {"Authorization": GLA_API_KEY, "Accept": "application/json"}
    new_leads = []
    try:
        res = requests.get("https://planningdata.london.gov.uk/api/applications", params={"limit": 50}, headers=headers, timeout=20)
        if res.status_code == 200:
            records = res.json().get("data", [])
            conn = database.get_db_conn(); cur = conn.cursor()
            for item in records:
                summary = item.get("proposal", "")
                if not any(word in summary.lower() for word in TREE_GOLD): continue
                ref = item.get("reference", f"LON-{int(time.time())}")
                addr = item.get("address", "Greater London")
                cur.execute("INSERT INTO leads (reference, address, summary, council_source) VALUES (%s, %s, %s, 'London Hub') ON CONFLICT DO NOTHING RETURNING id;", (ref, addr, summary[:350]))
                if cur.fetchone(): new_leads.append({"ref": ref, "addr": addr, "summary": summary})
            conn.commit(); cur.close(); conn.close()
            notifications.dispatch_lead_alerts("London", new_leads)
    except: pass
    return len(new_leads)

def scan_leeds_leads():
    """Scout goes to the Leeds Council portal."""
    url = "https://mapservices.leeds.gov.uk/arcgis/rest/services/Public/Planning/MapServer/12/query"
    params = {"where": "1=1", "outFields": "*", "resultRecordCount": 50, "f": "json"}
    new_leads = []
    try:
        res = requests.get(url, params=params, timeout=30, verify=False)
        raw = [f.get("attributes", {}) for f in res.json().get("features", [])]
        conn = database.get_db_conn(); cur = conn.cursor()
        for rec in raw:
            summary = str(rec.get("DESCRIPTION") or "")
            if not any(word in summary.lower() for word in TREE_GOLD): continue
            ref = str(rec.get("REFERENCE") or rec.get("OBJECTID"))
            addr = rec.get("ADDRESS") or "Leeds"
            cur.execute("INSERT INTO leads (reference, address, summary, council_source) VALUES (%s, %s, %s, 'Leeds') ON CONFLICT DO NOTHING RETURNING id;", (ref, addr, summary[:350]))
            if cur.fetchone(): new_leads.append({"ref": ref, "addr": addr, "summary": summary})
        conn.commit(); cur.close(); conn.close()
        notifications.dispatch_lead_alerts("Leeds", new_leads)
    except: pass
    return len(new_leads)