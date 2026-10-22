"""Oct 22 2026: login-cookie signing secret never silently becomes a public constant on Render."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import test_access_control  # noqa: F401  (stubs + imports main)
import main


class TestSessionSecret(unittest.TestCase):
    def test_session_secret_wins(self):
        s, src = main._resolve_session_secret({"SESSION_SECRET": " abc ", "TRIGGER_SECRET": "t", "RENDER": "true"})
        self.assertEqual((s, src), (b"abc", "SESSION_SECRET"))

    def test_trigger_secret_is_the_existing_fallback(self):
        s, src = main._resolve_session_secret({"TRIGGER_SECRET": "t"})
        self.assertEqual((s, src), (b"t", "TRIGGER_SECRET"))

    def test_render_with_nothing_set_is_random_not_the_public_default(self):
        a, src = main._resolve_session_secret({"RENDER": "true"})
        b, _ = main._resolve_session_secret({"RENDER": "true"})
        self.assertEqual(src, "random")
        self.assertNotEqual(a, b"treekey-fallback-dev-secret")
        self.assertNotEqual(a, b)
        self.assertGreaterEqual(len(a), 32)

    def test_local_dev_keeps_the_dev_default(self):
        self.assertEqual(main._resolve_session_secret({})[1], "dev-default")


if __name__ == "__main__":
    unittest.main()
