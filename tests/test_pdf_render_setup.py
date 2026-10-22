"""
test_pdf_render_setup.py -- the production PDF-rendering setup (Chromium for letter PDFs).

Covers: where Chromium is looked for at runtime (project folder, never overriding an explicit setting),
the admin check route (unauthorised, success, failure), the build script wiring, and that the check
sends nothing / touches no database. A REAL render is checked by scripts/pdf_render_check.py
(needs Chromium; run by render_build_browser.sh during the Render build).

Run: python -m unittest test_pdf_render_setup
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _ROOT)
sys.path.insert(0, _THIS_DIR)

import test_access_control  # noqa: E402,F401  (stubs)
import main  # noqa: E402
from letter_providers import intelliprint_provider as ip  # noqa: E402
from fastapi import HTTPException  # noqa: E402
from test_letter_setup_checkout_gate import _mock_request  # noqa: E402


class TestBrowserLocation(unittest.TestCase):
    def test_uses_project_folder_when_it_exists_and_nothing_is_set(self):
        with tempfile.TemporaryDirectory() as d, \
             patch.object(ip, "_project_browsers_dir", return_value=d), \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
            ip._use_project_browsers()
            self.assertEqual(os.environ["PLAYWRIGHT_BROWSERS_PATH"], d)

    def test_explicit_setting_is_respected(self):
        with tempfile.TemporaryDirectory() as d, \
             patch.object(ip, "_project_browsers_dir", return_value=d), \
             patch.dict(os.environ, {"PLAYWRIGHT_BROWSERS_PATH": "/somewhere/else"}):
            ip._use_project_browsers()
            self.assertEqual(os.environ["PLAYWRIGHT_BROWSERS_PATH"], "/somewhere/else")

    def test_nothing_set_when_folder_missing(self):
        with patch.object(ip, "_project_browsers_dir", return_value="/no/such/folder"), \
             patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PLAYWRIGHT_BROWSERS_PATH", None)
            ip._use_project_browsers()
            self.assertNotIn("PLAYWRIGHT_BROWSERS_PATH", os.environ)

    def test_project_folder_is_inside_the_project(self):
        self.assertEqual(ip._project_browsers_dir(), os.path.join(_ROOT, ".playwright-browsers"))

    def test_render_function_selects_the_location_before_starting_playwright(self):
        order = []
        fake_pw = MagicMock()
        fake_pw.sync_playwright.side_effect = lambda: (_ for _ in ()).throw(RuntimeError("stop"))
        with patch.object(ip, "_use_project_browsers", side_effect=lambda: order.append("path")), \
             patch.dict(sys.modules, {"playwright": MagicMock(), "playwright.sync_api": fake_pw}):
            with self.assertRaises(RuntimeError):
                ip._render_html_to_pdf_bytes("<p>x</p>")
        self.assertEqual(order, ["path"])


class TestSelfCheck(unittest.TestCase):
    def test_rejects_a_pdf_with_the_wrong_page_count(self):
        with patch.object(ip, "_render_html_to_pdf_bytes", return_value=b"%PDF-1.4 /Type /Page"):
            with self.assertRaises(RuntimeError):
                ip.pdf_render_selfcheck()

    def test_accepts_a_two_page_pdf(self):
        with patch.object(ip, "_render_html_to_pdf_bytes", return_value=b"%PDF-1.4 /Type /Page /Type /Page /Type /Pages"):
            r = ip.pdf_render_selfcheck()
        self.assertTrue(r["ok"])
        self.assertEqual(r["pages"], 2)


class TestAdminRoute(unittest.TestCase):
    def test_unauthorised_is_refused_and_nothing_renders(self):
        with patch.object(ip, "pdf_render_selfcheck") as chk:
            with self.assertRaises(HTTPException) as cm:
                main.admin_pdf_render_check(_mock_request(), None)
        self.assertEqual(cm.exception.status_code, 401)
        chk.assert_not_called()

    def test_success_returns_the_result(self):
        with patch.object(main, "verify_admin_or_secret", return_value=True), \
             patch.object(ip, "pdf_render_selfcheck", return_value={"ok": True, "pages": 2}):
            self.assertEqual(main.admin_pdf_render_check(_mock_request(), "s"), {"ok": True, "pages": 2})

    def test_failure_is_http_500_with_the_reason(self):
        with patch.object(main, "verify_admin_or_secret", return_value=True), \
             patch.object(ip, "pdf_render_selfcheck", side_effect=RuntimeError("Executable doesn't exist")):
            with self.assertRaises(HTTPException) as cm:
                main.admin_pdf_render_check(_mock_request(), "s")
        self.assertEqual(cm.exception.status_code, 500)
        self.assertFalse(cm.exception.detail["ok"])
        self.assertIn("Executable", cm.exception.detail["error"])

    def test_route_never_touches_database_or_worker(self):
        with patch.object(main, "verify_admin_or_secret", return_value=True), \
             patch.object(ip, "pdf_render_selfcheck", return_value={"ok": True}), \
             patch.object(main.database, "get_db_conn") as db, \
             patch("worker.run_one_pass") as w:
            main.admin_pdf_render_check(_mock_request(), "s")
        db.assert_not_called()
        w.assert_not_called()


class TestBuildScript(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(_ROOT, "render_build_browser.sh")) as f:
            self.script = f.read()

    def test_installs_into_the_project_folder_then_renders_a_pdf(self):
        self.assertIn('PLAYWRIGHT_BROWSERS_PATH="$PWD/.playwright-browsers"', self.script)
        self.assertIn("python -m playwright install chromium", self.script)
        self.assertLess(self.script.index("playwright install chromium"), self.script.index("pdf_render_check.py"))
        self.assertIn("set -euo pipefail", self.script)

    def test_script_and_runtime_use_the_same_folder_name(self):
        self.assertTrue(ip._project_browsers_dir().endswith(".playwright-browsers"))

    def test_folder_is_not_committed(self):
        with open(os.path.join(_ROOT, ".gitignore")) as f:
            self.assertIn(".playwright-browsers/", f.read())


if __name__ == "__main__":
    unittest.main()
