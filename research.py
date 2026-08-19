import os
import base64
import requests
import time
import logging
import database
from dotenv import load_dotenv

load_dotenv()
CH_KEY = os.getenv("COMPANIES_HOUSE_KEY", "").strip()
APOLLO_API_KEY = os.getenv("APOLLO_API_KEY", "").strip()
GOOGLE_MAPS_KEY = os.getenv("GOOGLE_MAPS_KEY", "").strip()

def get_google_rating(company_name, city):
    """Pillar 3: Checks Google for reputation filtering."""
    if not GOOGLE_MAPS_KEY:
        return None
    try:
        search_url = "https://maps.googleapis.com/maps/api/place/textsearch/json"
        query = f"{company_name} in {city}"
        res = requests.get(search_url, params={"query": query, "key": GOOGLE_MAPS_KEY}, timeout=10)
        results = res.json().get("results", [])
        if results:
            return results[0].get("rating")
    except Exception as e:
        logging.error(f"[Google] Error fetching rating: {e}")
    return None

def enrich_with_apollo(company_name, city):
    """Pillar 2: Investigator goes to Apollo to find the boss's name."""
    if not APOLLO_API_KEY:
        return None, None
    try:
        url = "https://api.apollo.io/v1/people/search"
        data = {
            "api_key": APOLLO_API_KEY, 
            "q_organization_name": company_name, 
            "person_titles": ["Owner", "Managing Director", "Director", "Founder"]
        }
        res = requests.post(url, json=data, timeout=10)
        if res.status_code == 200:
            people = res.json().get("people", [])
            if people:
                return people[0].get("name"), people[0].get("sanitized_phone")
    except Exception as e:
        logging.error(f"[Apollo] Error: {e}")
    return None, None

def perform_research(city_name):
    """Finds Tree Surgery LTD companies, filters for Golden Rule, and enriches."""
    auth = base64.b64encode(f"{CH_KEY}:".encode()).decode()
    headers = {"Authorization": f"Basic {auth}"}
    
    try:
        conn = database.get_db_conn()
        cur = conn.cursor()
        
        res = requests.get("https://api.company-information.service.gov.uk/search/companies", 
                           params={"q": f"tree surgery {city_name}"}, headers=headers, timeout=15)
        
        if res.status_code == 200:
            items = res.json().get('items', [])
            for co in items:
                name = co.get('title', '').upper()
                
                # GOLDEN RULE ENFORCEMENT: Strictly Limited Companies Only
                is_ltd = any(term in name for term in ["LTD", "LIMITED"])
                if not is_ltd or co.get('company_status') != "active":
                    continue
                
                # Pillar 2: Apollo (Managing Director Discovery)
                md_name, md_phone = enrich_with_apollo(name, city_name)
                
                # Pillar 3: Google (Reputation Rating)
                rating = get_google_rating(name, city_name)
                
                cur.execute("""
                    INSERT INTO potential_partners (
                        company_name, company_number, target_city, 
                        md_name, phone_number, google_rating, status
                    ) 
                    VALUES (%s, %s, %s, %s, %s, %s, 'enriched') 
                    ON CONFLICT (company_number) 
                    DO UPDATE SET 
                        md_name = COALESCE(EXCLUDED.md_name, potential_partners.md_name),
                        google_rating = COALESCE(EXCLUDED.google_rating, potential_partners.google_rating);
                """, (name, co.get('company_number'), city_name, md_name, md_phone, rating))
        
        conn.commit()
        cur.close()
        conn.close()
        logging.info(f"[Investigator] Completed research for {city_name}.")
    except Exception as e:
        logging.error(f"[Investigator] Error in perform_research: {e}")