"""
test_install_experience.py -- the "install TreeKey" experience: computers get "Get TreeKey on your phone" with a QR code to
/get-app and never an install prompt, Android keeps the browser's own install prompt, iPhone gets accurate Safari steps,
and nothing is promoted inside an installed app. (Real-browser behaviour: tests/install_browser_check.py.)

Run: python -m unittest test_install_experience
"""
import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_APP_DIR = os.path.dirname(_THIS_DIR)
sys.path.insert(0, _APP_DIR)
sys.path.insert(0, _THIS_DIR)

import test_access_control  # noqa: E402,F401  (stubs)
import main  # noqa: E402
import home_v2  # noqa: E402


def _node_ok(js: str):
    if not shutil.which("node"):
        return None
    f = tempfile.NamedTemporaryFile("w", suffix=".js", delete=False)
    f.write(js)
    f.close()
    try:
        r = subprocess.run(["node", "--check", f.name], capture_output=True, text=True)
        return r.returncode == 0, r.stderr
    finally:
        os.unlink(f.name)


def _legacy_install_script() -> str:
    """The inline script of the older page template that holds the install button (as the browser receives it)."""
    tree = ast.parse(open(os.path.join(_APP_DIR, "main.py"), encoding="utf-8").read())
    for n in ast.walk(tree):
        if isinstance(n, ast.JoinedStr):
            txt = "".join(v.value if isinstance(v, ast.Constant) else "0" for v in n.values)
            if "pwaInstallLabel" in txt:
                for m in re.finditer(r"<script[^>]*>(.*?)</script>", txt, re.S):
                    if "tkInstallApp = function" in m.group(1):
                        return m.group(1)
    raise AssertionError("legacy install script not found")


def _home_install_script() -> str:
    for m in re.finditer(r"<script>(.*?)</script>", home_v2._SCRIPT, re.S):
        if "pwaInstallBtn" in m.group(1):
            return m.group(1)
    raise AssertionError("home install script not found")


class TestHomePageInstall(unittest.TestCase):
    def test_script_is_valid_javascript(self):
        r = _node_ok(_home_install_script())
        if r is None:
            self.skipTest("node not available")
        self.assertTrue(r[0], r[1])

    def test_older_template_script_is_valid_javascript(self):
        # The previous desktop popover nested single quotes and broke the WHOLE script on these pages.
        r = _node_ok(_legacy_install_script())
        if r is None:
            self.skipTest("node not available")
        self.assertTrue(r[0], r[1])

    def test_desktop_branch_has_qr_and_never_prompts(self):
        js = _home_install_script()
        desktop = js[js.index("if (isDesktop) {"):js.index("} else if (isIOS) {")]
        self.assertIn("Get TreeKey on your phone", desktop)
        self.assertIn("/static/images/get-app-qr.png", desktop)
        self.assertNotIn(".prompt(", desktop)
        self.assertIn("e.preventDefault()", desktop)

    def test_android_branch_uses_native_prompt_and_no_apple_text(self):
        js = _home_install_script()
        android = js[js.index("/* Android and other phone browsers"):]
        self.assertIn("beforeinstallprompt", android)
        self.assertIn("d.prompt()", android)
        self.assertNotIn("Home Screen", android)

    def test_installed_app_hides_promotion(self):
        for js in (_home_install_script(), _legacy_install_script()):
            self.assertIn("display-mode: standalone", js)
            self.assertIn("navigator.standalone", js)
            self.assertIn("appinstalled", js)

    def test_iphone_steps_are_accurate_and_not_two_taps(self):
        header = home_v2._header_html(None)
        self.assertNotIn("quick taps", header.lower())
        self.assertNotIn("Two quick", header)
        for needle in ("Share", "Add to Home Screen", "3. Tap", "Add"):
            self.assertIn(needle, header)
        self.assertNotIn("Download app", header)

    def test_old_qr_target_and_wording_are_gone(self):
        self.assertNotIn("app-install-qr.png", _home_install_script() + _legacy_install_script())
        self.assertNotIn("TreeKey is a phone app", _home_install_script() + _legacy_install_script())

    def test_qr_image_points_at_get_app(self):
        try:
            import cv2
        except ImportError:
            self.skipTest("opencv not available")
        img = cv2.imread(os.path.join(_APP_DIR, "static", "images", "get-app-qr.png"))
        self.assertIsNotNone(img)
        self.assertEqual(cv2.QRCodeDetector().detectAndDecode(img)[0], "https://treekey.co.uk/get-app")


class TestGetAppPage(unittest.TestCase):
    def setUp(self):
        self.html = main._GET_APP_HTML

    def test_route_exists_and_returns_the_page(self):
        src = open(os.path.join(_APP_DIR, "main.py"), encoding="utf-8").read()
        self.assertIn('@app.get("/get-app"', src)
        self.assertIsNotNone(main.get_app_page())

    def test_page_is_installable_context(self):
        self.assertIn('rel="manifest"', self.html)
        self.assertIn("serviceWorker", self.html)
        self.assertIn("beforeinstallprompt", self.html)
        self.assertIn("d.prompt()", self.html)

    def test_page_is_valid_javascript(self):
        js = re.findall(r"<script>(.*?)</script>", self.html, re.S)
        r = _node_ok("\n".join(js))
        if r is None:
            self.skipTest("node not available")
        self.assertTrue(r[0], r[1])

    def test_android_section_has_no_apple_wording(self):
        sec = self.html[self.html.index('<section id="s-android"'):self.html.index('<section id="s-other"')]
        self.assertNotIn("Safari", sec)
        self.assertNotIn("Share", sec)
        self.assertNotIn("Home Screen", sec)

    def test_ios_section_steps(self):
        sec = self.html[self.html.index('<section id="s-ios"'):self.html.index('<section id="s-android"')]
        for needle in ("Safari", "Share", "Add to Home Screen", "<b>Add</b>"):
            self.assertIn(needle, sec)
        self.assertNotIn("two quick", sec.lower())

    def test_installed_and_other_devices_are_handled(self):
        self.assertIn("s-installed", self.html)
        self.assertIn("display-mode: standalone", self.html)
        self.assertIn("get-app-qr.png", self.html)


if __name__ == "__main__":
    unittest.main()
