"""Build-time / on-demand check: render a sample PDF with the production browser setup.
Sends nothing and touches no database. Exit code 0 = a real 2-page PDF was produced."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from letter_providers.intelliprint_provider import pdf_render_selfcheck  # noqa: E402

try:
    print("PDF render check OK:", pdf_render_selfcheck())
except Exception as exc:  # noqa: BLE001
    print("PDF render check FAILED:", exc)
    sys.exit(1)
