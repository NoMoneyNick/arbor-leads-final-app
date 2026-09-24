"""
test_slash_reference_routing.py -- 2026-09-24 handoff.

Nick's report: "Letter" link 404'd, and a previous pass's styling fix did
not reproduce or fix it -- root cause was explicitly flagged as unconfirmed.

REPRODUCED AND FIXED this pass: a historical claim's buyer-facing reference
is the real council reference, unchanged (address_release.buyer_facing_
reference's own docstring), and a genuine UK planning reference routinely
contains literal "/" characters (see main.py's own real reference examples,
e.g. "26/P/1118/S73" in _SUSPECT_DISCHARGE_REFS_SEP11). The links main.py
builds with urllib.parse.quote() do NOT escape "/" (its default `safe` is
'/'), so the href generated for exactly this population was a multi-segment
path Starlette's default single-segment `{param}` route convertor can never
match -- a genuine 404 from the router itself, before generate_homeowner_
letter/generate_street_flyer/street_view_redirect ever run. No amount of
fixing what those functions RETURN (the earlier styling pass) could ever
have reached this: the router never dispatches to them at all.

WHY THIS ISN'T TESTED VIA tests/test_access_control.py & co: this whole
test suite runs main.py against a hand-rolled `fastapi` stub (see that
file's own sys.modules setup) whose `@app.get(...)` is a pure pass-through
decorator -- it never parses or compiles the route's path template, so it
cannot catch a route-matching bug like this one, and wouldn't have caught
either the bug or a correct fix. This file deliberately does NOT import
main.py or use that stub at all. It imports the REAL, installed `starlette`
package (a genuine dependency of this project, confirmed installed in this
environment; only `fastapi` itself is unavailable here) and exercises the
exact path templates main.py's route decorators actually use -- extracted
by reading main.py's own source text, not retyped by hand, so this test
fails the moment those decorator strings drift from what it checks -- via
starlette.routing's real Route class and TestClient. FastAPI's APIRoute is
a direct subclass of starlette.routing.Route and uses the identical
compile_path()-based matching with no reimplementation of its own, so this
is a faithful reproduction of the real app's actual routing behaviour, not
an approximation of it.

Run with:
    python -m unittest tests.test_slash_reference_routing -v
"""
import os
import re
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Under `unittest discover`, this file collects AFTER tests/test_access_
# control.py (alphabetically: "access" < "slash"), which -- when "fastapi"
# isn't already in sys.modules, true the first time it runs -- registers a
# bare, minimal FAKE "starlette"/"starlette.exceptions" in sys.modules
# (its own fastapi-stub setup needs starlette.exceptions.HTTPException to
# exist for main.py's `from starlette.exceptions import HTTPException`-
# style calls, and doesn't need the real package for that). discover()
# finishes importing every test file before running any of them, so that
# fake replacement is already in sys.modules by the time this file is
# even imported, let alone run -- `from starlette.applications import
# Starlette` then fails, because the fake is a bare types.ModuleType with
# no submodules, not the real installed package. This module deliberately
# needs the REAL, installed starlette (see the module docstring above) --
# not any fastapi/starlette stub -- so drop whatever fake entries are
# there and let Python do a genuine fresh import of the real on-disk
# package, which is unaffected by any of this (only sys.modules's cache
# was poisoned, not the package itself).
for _name in list(sys.modules):
    if _name == "starlette" or _name.startswith("starlette."):
        del sys.modules[_name]

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

_MAIN_PY = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py")


def _extract_route_path(url_prefix: str) -> str:
    """Reads main.py's own source and pulls the exact path template given
    to @app.get(...) for the route starting with `url_prefix` -- never
    hand-retyped, so this test can't silently drift from the real
    decorator string it's meant to be checking."""
    with open(_MAIN_PY, "r", encoding="utf-8") as f:
        src = f.read()
    pattern = r'@app\.get\("(' + re.escape(url_prefix) + r'[^"]*)"'
    match = re.search(pattern, src)
    if not match:
        raise AssertionError(f"Could not find an @app.get(\"{url_prefix}...\") route decorator in main.py "
                              f"-- has this route been renamed or removed?")
    return match.group(1)


# A real UK planning reference, exactly as it appears in main.py's own
# _SUSPECT_DISCHARGE_REFS_SEP11 list -- not a synthetic worst case.
_REAL_SLASH_REFERENCE = "26/P/1118/S73"


def _build_single_route_app(path_template: str) -> Starlette:
    async def handler(request):
        return PlainTextResponse(request.path_params.get("lead_id") or request.path_params.get("reference") or "")
    return Starlette(routes=[Route(path_template, handler)])


class TestGenerateLetterRouteMatchesSlashContainingReferences(unittest.TestCase):
    def setUp(self):
        self.path_template = _extract_route_path("/generate-letter/")
        self.client = TestClient(_build_single_route_app(self.path_template))

    def test_route_declares_the_path_convertor_not_the_default_single_segment_one(self):
        self.assertIn(":path}", self.path_template,
                      "generate-letter's route must use Starlette's `path` convertor "
                      "({lead_id:path}) -- the default convertor cannot match a reference "
                      "containing '/', which is a normal, common shape for a real UK "
                      "planning reference (see _SUSPECT_DISCHARGE_REFS_SEP11).")

    def test_a_real_slash_containing_council_reference_reaches_the_handler(self):
        response = self.client.get(f"/generate-letter/{_REAL_SLASH_REFERENCE}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.text, _REAL_SLASH_REFERENCE)

    def test_a_plain_reference_still_works_no_regression(self):
        response = self.client.get("/generate-letter/PLANIT-REF-001")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "PLANIT-REF-001")

    def test_an_opaque_allocation_uuid_still_works(self):
        response = self.client.get("/generate-letter/3fa85f64-5717-4562-b3fc-2c963f66afa6")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "3fa85f64-5717-4562-b3fc-2c963f66afa6")


class TestGenerateStreetFlyerRouteMatchesSlashContainingReferences(unittest.TestCase):
    def setUp(self):
        self.path_template = _extract_route_path("/generate-street-flyer/")
        self.client = TestClient(_build_single_route_app(self.path_template))

    def test_route_declares_the_path_convertor(self):
        self.assertIn(":path}", self.path_template)

    def test_a_real_slash_containing_council_reference_reaches_the_handler(self):
        response = self.client.get(f"/generate-street-flyer/{_REAL_SLASH_REFERENCE}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.text, _REAL_SLASH_REFERENCE)


class TestStreetViewRouteMatchesSlashContainingReferences(unittest.TestCase):
    def setUp(self):
        self.path_template = _extract_route_path("/street-view/")
        self.client = TestClient(_build_single_route_app(self.path_template))

    def test_route_declares_the_path_convertor(self):
        self.assertIn(":path}", self.path_template)

    def test_a_real_slash_containing_council_reference_reaches_the_handler(self):
        response = self.client.get(f"/street-view/{_REAL_SLASH_REFERENCE}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.text, _REAL_SLASH_REFERENCE)


class TestUnescapedQuoteOfASlashContainingReferenceIsWhatActuallyShipsAsALink(unittest.TestCase):
    """Confirms the OTHER half of the bug: urllib.parse.quote(), as called
    everywhere main.py builds one of these links, does not escape '/' by
    default -- so the link this app actually generates for a historical
    claim is exactly the unescaped multi-segment path exercised above, not
    a %2F-encoded one. (Also confirms encoding it WOULD NOT have been a
    sufficient fix on its own: Starlette decodes %2F back to '/' before
    route matching, so a percent-encoded slash 404s against the default
    convertor exactly the same as a literal one -- only the `path`
    convertor actually fixes this.)"""

    def test_default_quote_leaves_the_slash_unescaped(self):
        import urllib.parse
        self.assertEqual(urllib.parse.quote(_REAL_SLASH_REFERENCE), _REAL_SLASH_REFERENCE)

    def test_percent_encoded_slash_still_404s_against_the_default_convertor(self):
        async def handler(request):
            return PlainTextResponse(request.path_params["lead_id"])
        app = Starlette(routes=[Route("/generate-letter/{lead_id}", handler)])
        client = TestClient(app)
        import urllib.parse
        encoded = urllib.parse.quote(_REAL_SLASH_REFERENCE, safe="")
        response = client.get(f"/generate-letter/{encoded}")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
