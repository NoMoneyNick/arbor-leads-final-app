#!/usr/bin/env python3
"""
scripts/intelliprint_auth_check.py -- run this yourself, locally, BEFORE
(or instead of) re-running scripts/intelliprint_test_send.py, to isolate
whether your Intelliprint account/key problem is an authentication issue
at all, separately from print-job submission.

WHY THIS EXISTS (2026-09-26)
-----------------------------
Your test run of intelliprint_test_send.py got back:
    HTTP Error 403: Forbidden
    Outcome: unknown
    Provider reference: None

Per Intelliprint's own documented error codes (intelliprint.net/docs/errors),
a 403 with code "forbidden" specifically means "The API key provided was
not authorised to access the requested resource" -- different from a bad
or missing key entirely (401, codes invalid_api_key/no_api_key). That
distinction matters, but intelliprint_test_send.py calls
POST /v1/prints -- creating a print job -- which bundles together two
different things that could each independently cause a 403: (a) your key
not being authenticated/authorised at all, or (b) your key being valid
but this specific account lacking some permission needed to CREATE a
print job specifically (e.g. an activation step, a missing payment
method on file, or a plan/scope restriction).

This script calls a completely different, DOCUMENTED, READ-ONLY endpoint
instead: GET /v1/prints (list your account's print jobs). It creates
nothing, charges nothing, and sends nothing -- regardless of testmode,
because no print job is ever submitted by this script at all. If THIS
call also 403s, the problem is authentication/authorisation on the
account or key itself, not anything specific to submitting print jobs.
If THIS call succeeds but submission still 403s, that narrows the
problem to a permission/activation gap around print-job creation
specifically -- worth raising with Intelliprint support with exactly that
detail.

WHAT THIS DOES
---------------
1. Reads INTELLIPRINT_API_KEY from your local .env file (same as
   intelliprint_test_send.py) -- never printed, never sent anywhere but
   Intelliprint's own API in the Authorization header.
2. Sends ONE GET request to https://api.intelliprint.net/v1/prints --
   Intelliprint's own documented "list print jobs" endpoint. No data is
   submitted; this cannot create a job, charge you, or post anything.
3. On success: prints only the HTTP status and, if your account has any
   existing print jobs, each one's id and status ONLY -- never address,
   name, or any other recipient/personal data, even though the real API
   response may contain that for past jobs on your account.
4. On failure: reuses this project's own safe error-classification logic
   (letter_providers/intelliprint_provider.py's _classify_http_error) --
   the exact same code path just added to fix the swallowed-diagnostics
   bug -- so you get the same safe, non-secret detail: HTTP status, an
   allow-listed subset of response headers (never Authorization, cookies,
   or anything key/token/secret-shaped), and Intelliprint's own error
   type/code/message if the response matches their documented error
   shape, or the raw (truncated, non-secret) body if it doesn't.

WHAT THIS DELIBERATELY DOES NOT DO
------------------------------------
- Never calls POST /v1/prints or any other write endpoint.
- Never prints your API key, any Authorization header value, or any
  recipient/personal data that might exist in your account's real print
  history.
- Does not touch this project's database.

HOW TO RUN
-----------
Just double-click RUN_INTELLIPRINT_AUTH_CHECK.bat in this project folder.
It runs this script and pauses so you can read or screenshot the result --
the output never contains your API key, so it's safe to share, including
with Intelliprint support if that turns out to be the next step.

(If you'd rather run it yourself: `python scripts\\intelliprint_auth_check.py`
from this folder, with your .env file already set up per
.env.example.letter-fulfilment's INTELLIPRINT_API_KEY section.)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
from pathlib import Path
from urllib.request import Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("NOTE: python-dotenv isn't installed, so this script cannot read a .env file "
          "automatically. Either `pip install python-dotenv` (same as the rest of this "
          "project needs), or set INTELLIPRINT_API_KEY as a real Windows environment "
          "variable instead.\n")

from letter_providers.intelliprint_provider import (
    API_BASE, IntelliprintProvider, _NoRedirects, _classify_http_error,
)


def main() -> int:
    print("=" * 70)
    print("READ-ONLY AUTH CHECK -- Intelliprint API")
    print("This makes ONE read-only call. It cannot create a print job,")
    print("charge anything, or post anything, regardless of test mode.")
    print("This output never includes your API key or any credential value.")
    print("=" * 70)
    print()

    provider = IntelliprintProvider()

    if not provider.is_configured():
        print("INTELLIPRINT_API_KEY is not set.")
        print()
        print("Add it to a file named .env in this project folder (create it if it doesn't")
        print("exist) -- see the INTELLIPRINT_API_KEY section in .env.example.letter-fulfilment")
        print("for exactly where to get the key from your Intelliprint account dashboard.")
        return 1

    print("Calling GET /v1/prints (Intelliprint's documented read-only 'list print jobs'")
    print("endpoint) -- this cannot create a job, charge anything, or post anything.")
    print()

    http_request = Request(f"{API_BASE}/prints", headers=provider._headers(), method="GET")

    try:
        with build_opener(_NoRedirects()).open(http_request, timeout=30) as response:
            status_code = response.status if hasattr(response, "status") else response.getcode()
            data = json.loads(response.read(2_000_000).decode("utf-8"))
    except urllib.error.HTTPError as exc:
        result = _classify_http_error(exc, provider.name)
        print("=" * 70)
        print(f"READ-ONLY AUTH CHECK RESULT:   FAILED (HTTP {exc.code} {exc.reason})")
        print(f"Classified outcome:  {result.outcome}")
        print(f"Detail:              {result.message}")
        print("=" * 70)
        print()
        if exc.code in (401,):
            print("A 401 here means the key itself is not being accepted as valid at all --")
            print("check it was copied in full, with no extra spaces, and that it hasn't")
            print("been regenerated/revoked in your Intelliprint dashboard since you copied it.")
        elif exc.code == 403:
            print("A 403 on this read-only list endpoint means the key is not authorised for")
            print("basic account access at all -- this points at an account-level permission,")
            print("activation, or plan issue rather than anything specific to submitting print")
            print("jobs. Worth raising with Intelliprint support (hello@intelliprint.net) with")
            print("the detail above -- never your API key itself.")
        else:
            print(f"Unexpected status {exc.code} on a read-only endpoint -- see the detail above.")
        return 1
    except Exception as exc:
        print("=" * 70)
        print(f"READ-ONLY AUTH CHECK RESULT:   COULD NOT COMPLETE ({type(exc).__name__}: {exc})")
        print("=" * 70)
        print()
        print("This looks like a network/connection problem reaching Intelliprint's API at")
        print("all (DNS, firewall, proxy), rather than an authentication rejection -- worth")
        print("checking your internet connection or any corporate proxy/firewall settings.")
        return 1

    # Success -- print only non-personal fields (id/status), never
    # recipient name/address, even if your account has real print history.
    jobs = data.get("data") if isinstance(data, dict) else None
    if jobs is None and isinstance(data, list):
        jobs = data
    jobs = jobs or []

    print("=" * 70)
    print(f"READ-ONLY AUTH CHECK RESULT:   SUCCESS (HTTP {status_code})")
    print(f"Your API key IS authenticated and authorised to call this read-only endpoint.")
    print(f"Print jobs visible on this account: {len(jobs)}")
    for job in jobs[:10]:
        if isinstance(job, dict):
            print(f"  - id={job.get('id')!r} status={job.get('status')!r}")
    if len(jobs) > 10:
        print(f"  ... and {len(jobs) - 10} more (not listed)")
    print("=" * 70)
    print()
    print("Since this read-only call succeeded, your key is valid and authorised for basic")
    print("account access. If POST /v1/prints (actual submission, via")
    print("intelliprint_test_send.py) still returns 403, the problem is narrower than the")
    print("key itself -- most likely a permission, activation, or account-setup step")
    print("specific to CREATING print jobs. That's worth raising with Intelliprint support")
    print("(hello@intelliprint.net), quoting the 403 detail from your intelliprint_test_send.py")
    print("run -- never your API key.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
