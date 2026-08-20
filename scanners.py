import os
import requests
import time
import logging
import database
import notifications

logger = logging.getLogger("vector-data-labs")

GLA_API_KEY = os.getenv("GLA_API_KEY", "").strip()

# ── Lead Scoring ──────────────────────────────────────────────────────────────

LARGE_KEYWORDS = [
    "tpo", "tree preservation order", "conservation area", "woodland",
    "development", "several trees", "multiple trees", "commercial",
    "site clearance", "site works", "dangerous tree", "estate",
    "demolition", "contaminated", "application to carry out works"
]
MEDIUM_KEYWORDS = [
    "crown reduction", "crown lift", "crown thin", "fell", "felling",
    "removal", "pollarding", "overhanging", "storm damage",
    "deadwood", "works to trees", "urgent", "diseased"
]
SMALL_KEYWORDS = [
    "pruning", "hedge", "trim", "cutting", "maintenance",
    "inspection", "minor works", "lopping"
]

TREE_GOLD = ["tree", "arbor", "felling", "stump", "surgery", "crown", "tpo", "woodland", "hedge"]


def score_lead(summary: str) -> tuple:
    """
    Classifies a planning application as small / medium / large
    and returns the corresponding price.
    Returns: (lead_score: str, lead_price: int)
    """
    s = summary.lower()
    if any(k in s for k in LARGE_KEYWORDS):
        return "large", 75
    elif any(k in s for k in MEDIUM_KEYWORDS):
        return "medium", 50
    return "small", 25


def _is_tree_related(text: str) -> bool:
    return any(word in text.lower() for word in TREE_GOLD)


def _insert_lead(cur, reference: str, address: str, summary: str, source: str) -> dict | None:
    """
    Inserts a lead into the DB. Returns the lead dict if new, None if duplicate.
    """
    lead_score, lead_price = score_lead(summary)
    cur.execute(
        """
        INSERT INTO leads (reference, address, summary, council_source, lead_score, lead_price)
        VALUES (%s, %s, %s, %s, %s, %s)
        ON CONFLICT (reference) DO NOTHING
        RETURNING id;
        """,
        (reference, address, summary[:350], source, lead_score, lead_price)
    )
    if cur.fetchone():
        return {"ref": reference, "addr": address, "summary": summary,
                "lead_score": lead_score, "lead_price": lead_price}
    return None


# ── Leeds Scanner (ArcGIS) ────────────────────────────────────────────────────

def scan_leeds_leads() -> int:
    """Scout goes to the Leeds Council ArcGIS portal to find tree surgery jobs."""
    url = "https://mapservices.leeds.gov.uk/arcgis/rest/services/Public/Planning/MapServer/12/query"
    params = {"where": "1=1", "outFields": "*", "resultRecordCount": 50, "f": "json"}
    new_leads = []

    try:
        res = requests.get(url, params=params, timeout=30, verify=False)
        res.raise_for_status()
        features = res.json().get("features", [])

        if not features:
            logger.warning("[Leeds] ArcGIS returned no features.")
            return 0

        conn = database.get_db_conn()
        cur = conn.cursor()

        for feature in features:
            rec = feature.get("attributes", {})
            summary = str(rec.get("DESCRIPTION") or "")
            if not _is_tree_related(summary):
                continue
            ref = str(rec.get("REFERENCE") or rec.get("OBJECTID") or f"LDS-{int(time.time())}")
            addr = rec.get("ADDRESS") or "Leeds"
            lead = _insert_lead(cur, ref, addr, summary, "Leeds")
            if lead:
                new_leads.append(lead)

        conn.commit()
        cur.close()
        conn.close()

    except requests.exceptions.Timeout:
        logger.error("[Leeds] Request timed out after 30s.")
    except requests.exceptions.HTTPError as e:
        logger.error(f"[Leeds] HTTP error: {e.response.status_code} — {e.response.text[:200]}")
    except requests.exceptions.ConnectionError as e:
        logger.error(f"[Leeds] Connection error: {e}")
    except Exception as e:
        logger.error(f"[Leeds] Unexpected error: {e}", exc_info=True)

    if new_leads:
        notifications.dispatch_lead_alerts("Leeds", new_leads)
    logger.info(f"[Leeds] Scan complete. {len(new_leads)} new leads found.")
    return len(new_leads)


# ── London Scanner (GLA Datahub) ──────────────────────────────────────────────

def scan_london_leads() -> int:
    """Scout goes to the London GLA Datahub to find tree surgery planning applications."""
    if not GLA_API_KEY:
        logger.error("[London] GLA_API_KEY is not set. Aborting scan.")
        return 0

    headers = {"Authorization": GLA_API_KEY, "Accept": "application/json"}
    new_leads = []

    try:
        res = requests.get(
            "https://planningdata.london.gov.uk/api/applications",
            params={"limit": 50},
            headers=headers,
            timeout=20
        )
        res.raise_for_status()
        records = res.json().get("data", [])

        if not records:
            logger.warning("[London] GLA API returned no records.")
            return 0

        conn = database.get_db_conn()
        cur = conn.cursor()

        for item in records:
            summary = item.get("proposal", "")
            if not _is_tree_related(summary):
                continue
            ref = item.get("reference", f"LON-{int(time.time())}")
            addr = item.get("address", "Greater London")
            lead = _insert_lead(cur, ref, addr, summary, "London")
            if lead:
                new_leads.append(lead)

        conn.commit()
        cur.close()
        conn.close()

    except requests.exceptions.Timeout:
        logger.error("[London] Request timed out after 20s.")
    except requests.exceptions.HTTPError as e:
        logger.error(f"[London] HTTP error: {e.response.status_code} — {e.response.text[:200]}")
    except requests.exceptions.ConnectionError as e:
        logger.error(f"[London] Connection error: {e}")
    except Exception as e:
        logger.error(f"[London] Unexpected error: {e}", exc_info=True)

    if new_leads:
        notifications.dispatch_lead_alerts("London", new_leads)
    logger.info(f"[London] Scan complete. {len(new_leads)} new leads found.")
    return len(new_leads)


# ── UK Planning API Scanner (Birmingham, Manchester, Bristol, Sheffield) ──────
# Uses ukplanningapi.co.uk — covers 289 UK councils, updated daily.
# Free tier: 500 req/month (no card). Paid: £99/month for 10,000 req.
# Sign up at: https://ukplanningapi.co.uk/api-signup
# Add key to Render as: UK_PLANNING_API_KEY

UK_PLANNING_API_KEY = os.getenv("UK_PLANNING_API_KEY", "").strip()

# Postcode area prefix per city — covers the full city in one query
CITY_POSTCODE_PREFIX = {
    "Birmingham": "B",
    "Manchester":  "M",
    "Bristol":     "BS",
    "Sheffield":   "S",
}


def scan_city_planning_api(city_name: str) -> int:
    """
    Scans planning applications for a UK city using ukplanningapi.co.uk.
    Covers Birmingham, Manchester, Bristol, Sheffield (and any future cities).
    API docs: https://ukplanningapi.co.uk/api-docs
    """
    if not UK_PLANNING_API_KEY:
        logger.error(f"[{city_name}] UK_PLANNING_API_KEY is not set. "
                     f"Get a free key at ukplanningapi.co.uk/api-signup")
        return 0

    postcode_prefix = CITY_POSTCODE_PREFIX.get(city_name)
    if not postcode_prefix:
        logger.error(f"[{city_name}] No postcode prefix configured for this city.")
        return 0

    headers = {"X-API-Key": UK_PLANNING_API_KEY}
    new_leads = []

    try:
        res = requests.get(
            "https://ukplanningapi.co.uk/v1/applications",
            params={
                "postcode": postcode_prefix,
                "status":   "received",
                "limit":    200,
            },
            headers=headers,
            timeout=20
        )

        if res.status_code == 429:
            logger.warning(f"[{city_name}] UK Planning API monthly quota reached. "
                           f"Upgrade at ukplanningapi.co.uk")
            return 0

        res.raise_for_status()
        records = res.json().get("data", [])

        if not records:
            logger.warning(f"[{city_name}] UK Planning API returned no results "
                           f"for postcode prefix '{postcode_prefix}'.")
            return 0

        conn = database.get_db_conn()
        cur = conn.cursor()

        for item in records:
            summary = item.get("description", "") or ""
            if not _is_tree_related(summary):
                continue
            ref  = item.get("reference") or f"{city_name[:3].upper()}-{int(time.time())}"
            addr = item.get("address", city_name)
            lead = _insert_lead(cur, ref, addr, summary, city_name)
            if lead:
                new_leads.append(lead)

        conn.commit()
        cur.close()
        conn.close()

    except requests.exceptions.Timeout:
        logger.error(f"[{city_name}] Request timed out.")
    except requests.exceptions.HTTPError as e:
        logger.error(f"[{city_name}] HTTP {e.response.status_code}: {e.response.text[:200]}")
    except requests.exceptions.ConnectionError as e:
        logger.error(f"[{city_name}] Connection error: {e}")
    except Exception as e:
        logger.error(f"[{city_name}] Unexpected error: {e}", exc_info=True)

    if new_leads:
        notifications.dispatch_lead_alerts(city_name, new_leads)
    logger.info(f"[{city_name}] Scan complete. {len(new_leads)} new leads found.")
    return len(new_leads)