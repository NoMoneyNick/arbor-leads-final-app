"""
test_migration_consistency_scraper_resilience.py -- migrations/
0002_scraper_resilience.sql claims to be a verbatim copy of the CREATE
TABLE/INDEX statements scraper_resilience.init_scraper_resilience_schema
executes. This test catches drift between the two (someone edits one and
forgets the other), following the exact same pattern
test_migration_consistency.py already established for
migrations/0001_letter_fulfilment.sql -- kept as a separate file/migration
rather than folded into that one because scraper_resilience.py is its own
bounded subsystem with its own migration file (see that module's own
docstring for why).

Run with:
    python -m unittest tests.test_migration_consistency_scraper_resilience -v
"""
import os
import re
import sys
import types
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if "database" not in sys.modules:
    sys.modules["database"] = types.ModuleType("database")

import scraper_resilience

_MIGRATION_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "migrations", "0002_scraper_resilience.sql"
)


def _tables_and_indexes_created_by(schema_fn) -> set:
    captured = []

    class _Capture:
        def execute(self, sql, params=None):
            captured.append(sql)

    schema_fn(_Capture())
    sql_text = "\n".join(captured)
    names = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", sql_text))
    names |= set(re.findall(r"CREATE (?:UNIQUE )?INDEX IF NOT EXISTS (\w+)", sql_text))
    return names


class TestMigrationFileCoversScraperResilienceSchema(unittest.TestCase):
    def setUp(self):
        with open(_MIGRATION_PATH) as f:
            self.migration_sql = f.read()

    def test_scraper_resilience_tables_and_indexes_are_all_in_the_migration_file(self):
        names = _tables_and_indexes_created_by(scraper_resilience.init_scraper_resilience_schema)
        missing = [n for n in names if n not in self.migration_sql]
        self.assertEqual(missing, [], f"migrations/0002_scraper_resilience.sql is missing: {missing}")
        # Sanity check the extraction itself found something -- an empty
        # `names` set would make the assertion above vacuously pass even if
        # the whole schema function silently broke.
        self.assertIn("source_incident", names)
        self.assertIn("repair_attempt", names)
        self.assertIn("council_scan_checkpoint", names)
        self.assertIn("council_scan_pass_metrics", names)

    def test_migration_file_declares_no_extra_tables_not_created_in_python(self):
        python_names = _tables_and_indexes_created_by(scraper_resilience.init_scraper_resilience_schema)
        sql_table_names = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)", self.migration_sql))
        extra = sql_table_names - python_names
        self.assertEqual(extra, set(),
                          f"migrations/0002_scraper_resilience.sql declares tables scraper_resilience.py doesn't create: {extra}")


if __name__ == "__main__":
    unittest.main()
