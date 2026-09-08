import database
try:
    conn = database.get_db_conn()
    cur = conn.cursor()
    cur.execute('SELECT count(*) FROM potential_partners')
    print('1 OK')
    cur.execute('SELECT count(*) FROM leads')
    print('2 OK')
    cur.execute("SELECT count(*) FROM leads WHERE lead_source_type IN ('direct_homeowner', 'domestic_classified')")
    print('3 OK')
    cur.execute('SELECT count(*) FROM potential_partners WHERE phone_number IS NOT NULL OR email IS NOT NULL')
    print('4 OK')
    cur.execute('SELECT company_name, md_name, target_city, google_rating, phone_number, email FROM potential_partners ORDER BY created_at DESC LIMIT 6')
    print('5 OK')
    cur.execute('SELECT address, summary, lead_score, lead_price, council_source, discovered_at FROM leads ORDER BY discovered_at DESC LIMIT 8')
    print('6 OK')
except Exception as e:
    print('ERROR:', e)
