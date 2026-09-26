#!/usr/bin/env python3
"""
scripts/intelliprint_test_send.py -- run this yourself, locally, to
empirically verify the real Intelliprint adapter against your actual
account, in TEST MODE ONLY.

WHAT THIS DOES
---------------
1. Reads INTELLIPRINT_API_KEY from a local .env file (never from chat,
   never from anywhere this script prints).
2. Submits exactly ONE print job to Intelliprint's real API, using the
   same safe, fictional sample letter/address TreeKey already uses for
   contractor letter previews (nothing of yours or a real homeowner's is
   sent).
3. testmode is HARD-CODED to true in this script, regardless of whatever
   INTELLIPRINT_TEST_MODE is set to in your .env -- this script can never
   trigger a real charge or a real posted letter, on purpose.
4. Prints the raw outcome (accepted/dispatched/rejected/unknown), the
   Intelliprint job id, and its status -- this is what "submission works
   and its status is recorded accurately" actually means empirically,
   not just in unit tests.
5. Immediately looks that job back up via check_status() (the same
   reconciliation path a stuck 'unknown' obligation would use), to prove
   that round-trip works too.

WHAT THIS DELIBERATELY DOES NOT DO
------------------------------------
- Does not touch your database, does not create a real lead/obligation,
  does not go anywhere near a real homeowner's address. This checks the
  new adapter itself (letter_providers/intelliprint_provider.py), which
  is the piece that was actually missing -- the database-backed
  obligation/retry/reconciliation pipeline around it was already built
  and tested against the fake provider in earlier work, and treats every
  adapter identically through the same interface.
- Does not print your API key anywhere, including in errors.

HOW TO RUN
-----------
Just double-click RUN_INTELLIPRINT_TEST.bat in this project folder (next
to UPDATE_WEBSITE.bat). It runs this script and pauses so you can read or
screenshot the result.

(If you'd rather run it yourself: `python scripts\\intelliprint_test_send.py`
from this folder, with your .env file already set up per
.env.example.letter-fulfilment's INTELLIPRINT_API_KEY section.)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("NOTE: python-dotenv isn't installed, so this script cannot read a .env file "
          "automatically. Either `pip install python-dotenv` (same as the rest of this "
          "project needs), or set INTELLIPRINT_API_KEY as a real Windows environment "
          "variable instead.\n")

# Hard override -- this script must never be able to trigger a real send,
# no matter what .env says.
os.environ["INTELLIPRINT_TEST_MODE"] = "true"

os.environ.setdefault("TREEKEY_PRIVACY_CONTACT_EMAIL", "privacy@treekey.co.uk")
os.environ.setdefault("TREEKEY_PRIVACY_POLICY_URL", "treekey.co.uk/privacy-policy")

import letter_content
from letter_providers.base import LetterRequest, fingerprint_content
from letter_providers.intelliprint_provider import IntelliprintProvider


def main() -> int:
    provider = IntelliprintProvider()

    if not provider.is_configured():
        print("INTELLIPRINT_API_KEY is not set.")
        print()
        print("Add it to a file named .env in this project folder (create it if it doesn't")
        print("exist) -- see the INTELLIPRINT_API_KEY section in .env.example.letter-fulfilment")
        print("for exactly where to get the key from your Intelliprint account dashboard.")
        return 1

    print(f"Using Intelliprint adapter. testmode={provider.test_mode} (forced true by this script).")
    print("Submitting one TEST-MODE print job using TreeKey's existing safe sample letter/address...")
    print()

    settings = letter_content.ContractorLetterSettings(
        contractor_email="test@example.com",
        business_name="Test Business (Intelliprint verification run)",
        phone="0113 555 0199",
        template_key="friendly_introduction",
    )
    html = letter_content.render_preview_letter(settings)

    request = LetterRequest(
        idempotency_key=f"intelliprint-verification-{os.getpid()}",
        lead_reference=letter_content.PREVIEW_LEAD_REFERENCE,
        address_lines={"line1": "123 Sample Street", "city": "Sample Town",
                        "postcode": "ST1 2AB", "country": "GB"},
        applicant_name="Sample Homeowner",
        content_html=html,
        content_fingerprint=fingerprint_content(html),
    )

    result = provider.send(request)

    print("=" * 70)
    print(f"OUTCOME:            {result.outcome}")
    print(f"Provider reference:  {result.provider_reference}")
    print(f"Cost (pence, ex/inc tax per Intelliprint's 'after_tax' field): {result.cost_pence}")
    print(f"Message:             {result.message}")
    print("=" * 70)
    print()

    if result.provider_reference:
        print("Looking the same job back up via check_status() (the reconciliation path)...")
        status_result = provider.check_status(result.provider_reference)
        if status_result is None:
            print("check_status() returned None -- could not reconcile (network issue, or")
            print("the account/key can't read this job back). Not necessarily a problem for")
            print("send() itself, but worth knowing.")
        else:
            print(f"Reconciled outcome:  {status_result.outcome}")
            print(f"Reconciled message:  {status_result.message}")

    print()
    if result.outcome == "accepted" or result.outcome == "dispatched":
        print("RESULT: Intelliprint accepted this test-mode submission and reported a status.")
        print("This confirms the real API integration works end-to-end in test mode.")
    elif result.outcome == "unknown":
        print("RESULT: Ambiguous outcome (timeout, network issue, or an unrecognised response")
        print("shape) -- held for reconciliation, nothing was resent. Check your network/proxy")
        print("if this persists, or share this whole output (never your API key) for help.")
    else:
        print("RESULT: Intelliprint reported this submission as rejected -- see the message")
        print("above for why (often an auth problem if the key is wrong, or a payload issue).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
