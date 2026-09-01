"""
test_database.py -- Unit tests for the pure logic in database.py that
doesn't need a real Postgres connection.

Run with:

    python -m unittest test_database.py -v

database.py imports psycopg2 (a real Postgres driver) at module load time.
Rather than requiring it installed just to test marketplace-filtering logic
that has nothing to do with the database connection itself, this file stubs
psycopg2 in sys.modules before importing database -- the same technique
test_scrapers.py already uses for database/notifications/dotenv. No real
network or DB call is made anywhere in this file.

Added Sep 2 2026, during the multi-vertical build: writing
test_hmo_lead_with_agent_is_not_excluded below is what caught a real bug
before it shipped further -- see _is_agent_already_handling_the_job's
Sep 2 2026 docstring in database.py for the full story. Short version: the
tree-specific "already has an agent, probably taken" exclusion rule was
being applied to every vertical, and since the tree-surgeon-name classifier
has no idea what an HMO conversion contractor's name looks like, it would
have silently excluded most real HMO leads with any agent on record from
the marketplace the moment that vertical started producing real leads.
"""
import importlib.util
import os
import sys
import types
import unittest
from unittest.mock import MagicMock

if "psycopg2" not in sys.modules:
    sys.modules["psycopg2"] = types.ModuleType("psycopg2")

# Sep 2 2026: when this file runs in the same process as test_scrapers.py
# (e.g. `python -m unittest test_scrapers.py test_idox.py test_database.py`),
# test_scrapers.py registers its OWN fake `database` module under
# sys.modules["database"] first (a stub with only get_db_conn/
# increment_api_usage mocked, for its own unrelated tests), and scanners.py
# (imported by test_scrapers.py) binds its own internal `database` name to
# that fake object. A plain `import database` here would just reuse
# whatever is already cached -- and popping/reassigning
# sys.modules["database"] instead was tried and rejected: scanners.py keeps
# its OWN reference to the module object from when IT first imported
# "database", so swapping the sys.modules entry afterwards doesn't change
# what scanners.py actually calls, while unittest.mock.patch("database.x")
# resolves against whatever sys.modules["database"] is NOW -- the two end
# up patching different objects and tests silently stop asserting anything
# real (caught by running the combined suite, not by this file alone).
# Loading database.py fresh under a private module name -- never touching
# sys.modules["database"] at all -- sidesteps this entirely and is safe
# regardless of what other test files import first, in either order.
_DATABASE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "database.py")
_spec = importlib.util.spec_from_file_location("_database_under_test", _DATABASE_PATH)
database = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(database)


class TestAgentAlreadyHandlingTheJob(unittest.TestCase):
    """_is_agent_already_handling_the_job -- the marketplace pre-purchase
    filter deciding whether a lead should be pulled from sale because the
    council record already names an agent apparently already doing the
    work. Wrong in one direction (excludes True) loses real revenue on a
    perfectly sellable lead; wrong the other way (excludes False) risks
    selling a job someone already has -- Nick's explicit red line."""

    # --- Tree vertical: must behave EXACTLY as before this refactor -------

    def test_tree_lead_with_confirmed_tree_agent_is_excluded(self):
        lead = {"vertical": "tree", "has_agent": True, "agent_is_tree_surgeon": True}
        self.assertTrue(database._is_agent_already_handling_the_job(lead))

    def test_tree_lead_with_confirmed_non_tree_agent_is_kept(self):
        lead = {"vertical": "tree", "has_agent": True, "agent_is_tree_surgeon": False}
        self.assertFalse(database._is_agent_already_handling_the_job(lead))

    def test_tree_lead_with_unconfirmed_agent_status_is_excluded(self):
        """The Aug 31 2026 rule: unknown (None) is treated the same as
        "confirmed tree surgeon" -- conservative by design, since almost
        the entire lead pool used to sit in this unconfirmed state."""
        lead = {"vertical": "tree", "has_agent": True, "agent_is_tree_surgeon": None}
        self.assertTrue(database._is_agent_already_handling_the_job(lead))

    def test_tree_lead_with_no_agent_at_all_is_kept(self):
        for has_agent in (False, None):
            with self.subTest(has_agent=has_agent):
                lead = {"vertical": "tree", "has_agent": has_agent, "agent_is_tree_surgeon": None}
                self.assertFalse(database._is_agent_already_handling_the_job(lead))

    def test_missing_vertical_key_defaults_to_tree_behaviour(self):
        """Rows written before the `vertical` column existed, or fetched
        without it for any reason, must keep the original tree-only
        behaviour -- matches the DB column's own DEFAULT 'tree'."""
        lead = {"has_agent": True, "agent_is_tree_surgeon": True}
        self.assertTrue(database._is_agent_already_handling_the_job(lead))

    # --- HMO (and any future non-tree) vertical: exempt from this check ---

    def test_hmo_lead_with_agent_is_not_excluded(self):
        """The bug this test caught: agent_is_tree_surgeon is a
        tree-surgeon-name classifier with no idea what an HMO conversion
        contractor's name looks like, so it returns None for almost every
        real HMO agent -- which the tree-only rule above treats as
        "excluded". Without the vertical scope, essentially every HMO lead
        with any agent on record would have silently never reached the
        marketplace."""
        lead = {"vertical": "hmo", "has_agent": True, "agent_is_tree_surgeon": None}
        self.assertFalse(database._is_agent_already_handling_the_job(lead))

    def test_hmo_lead_is_exempt_even_if_agent_is_tree_surgeon_happens_true(self):
        """Belt and braces: even in the unlikely case the classifier
        happens to return True for an HMO lead's agent text (e.g. it
        contains a coincidental tree-surgery-sounding word), the exclusion
        is still tree-vertical-only until a real HMO-specific classifier
        exists -- this is a deliberate, documented policy choice (favour
        never wrongly excluding a sellable lead), not an oversight."""
        lead = {"vertical": "hmo", "has_agent": True, "agent_is_tree_surgeon": True}
        self.assertFalse(database._is_agent_already_handling_the_job(lead))


if __name__ == "__main__":
    unittest.main()
