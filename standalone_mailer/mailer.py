"""Standalone UK letter submission. Python 3.10+, standard library only."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
from typing import Callable
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener


class MailerError(Exception):
    """Safe, user-facing failure."""


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def validate(letter: dict) -> dict:
    """Validate shape; this does NOT prove address deliverability or permission."""
    if not isinstance(letter, dict) or set(letter) != {"template_id", "recipient"}:
        raise MailerError("Supply exactly template_id and recipient.")
    if type(letter["template_id"]) is not int or letter["template_id"] <= 0:
        raise MailerError("template_id must be a positive integer.")
    recipient = letter["recipient"]
    if not isinstance(recipient, dict):
        raise MailerError("recipient must be an object.")
    for key, value in recipient.items():
        if not isinstance(key, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", key):
            raise MailerError("Invalid recipient field name.")
        if not isinstance(value, str) or len(value) > 4000 or any(ord(c) < 32 for c in value):
            raise MailerError("Recipient values must be single-line text, up to 4000 characters.")
    result = {k: v.strip() for k, v in recipient.items()}
    for key in ("address1", "city", "postcode", "country"):
        if not result.get(key):
            raise MailerError(f"Missing recipient field: {key}")
    if result["country"].upper() != "GB":
        raise MailerError("This starter module supports GB addresses only.")
    postcode = re.sub(r"\s+", "", result["postcode"].upper())
    if not re.fullmatch(r"(?:GIR0AA|[A-Z]{1,2}[0-9][A-Z0-9]?[0-9][A-Z]{2})", postcode):
        raise MailerError("Invalid UK postcode format.")
    result["postcode"] = postcode[:-3] + " " + postcode[-3:]
    result["country"] = "GB"
    return {"template_id": letter["template_id"], "recipient": result}


def stannp_transport(payload: dict, api_key: str) -> dict:
    """One request only: no automatic retries, no redirects, no key in URL."""
    token = base64.b64encode((api_key + ":").encode()).decode()
    request = Request(
        "https://api-eu1.stannp.com/v1/letters/create",
        data=urlencode(payload).encode(),
        headers={"Authorization": "Basic " + token,
                 "Content-Type": "application/x-www-form-urlencoded",
                 "Accept": "application/json"},
        method="POST",
    )
    with build_opener(NoRedirects()).open(request, timeout=30) as response:
        return json.loads(response.read(2_000_000).decode())


class Mailer:
    def __init__(self, database: str | Path = "mailing.sqlite3", *,
                 mode: str = "dry_run", api_key: str | None = None,
                 allow_live: bool = False,
                 transport: Callable[[dict, str], dict] = stannp_transport):
        if mode not in {"dry_run", "test", "live"}:
            raise MailerError("Mode must be dry_run, test or live.")
        if mode == "live" and not allow_live:
            raise MailerError("Live posting is disabled. Explicitly enable it first.")
        if mode != "dry_run" and (not isinstance(api_key, str) or not api_key.strip()):
            raise MailerError("STANNP_API_KEY is required for provider test/live mode.")
        self.database, self.mode = str(database), mode
        self.api_key, self.transport = api_key, transport
        with self._db() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                mode TEXT NOT NULL, request_key TEXT NOT NULL,
                fingerprint TEXT NOT NULL, state TEXT NOT NULL,
                result TEXT, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (mode, request_key))""")

    def _db(self):
        # One shared durable file is required across all local callers.
        db = sqlite3.connect(self.database, timeout=30)
        db.row_factory = sqlite3.Row
        return db

    def _finish(self, request_key: str, result: dict) -> dict:
        with self._db() as db:
            db.execute("""UPDATE jobs SET state=?, result=?, updated_at=CURRENT_TIMESTAMP
                WHERE mode=? AND request_key=?""",
                       (result["state"], json.dumps(result), self.mode, request_key))
        return result

    def status(self, request_key: str) -> dict | None:
        """Read local submission state. 'submitted' is NOT proof of delivery."""
        with self._db() as db:
            row = db.execute("SELECT * FROM jobs WHERE mode=? AND request_key=?",
                             (self.mode, request_key)).fetchone()
        if row is None:
            return None
        return (json.loads(row["result"]) if row["result"] else
                {"state": row["state"], "request_key": request_key, "mode": self.mode})

    def send(self, request_key: str, letter: dict) -> dict:
        """A stable business key blocks duplicate attempts, including across restarts.

        An ambiguous submission is held for manual provider reconciliation.
        Never use a new key just to retry an uncertain request.
        """
        if not isinstance(request_key, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,160}", request_key):
            raise MailerError("Use a stable request key: 1-160 letters, digits, _ . : or -.")
        letter = validate(letter)
        canonical = json.dumps(letter, sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(canonical.encode()).hexdigest()
        with self._db() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM jobs WHERE mode=? AND request_key=?",
                             (self.mode, request_key)).fetchone()
            if old:
                if old["fingerprint"] != fingerprint:
                    raise MailerError("This request key already belongs to different letter content.")
                if old["result"]:
                    return json.loads(old["result"])
                return {"state": "submitting", "mode": self.mode, "request_key": request_key,
                        "message": "In progress or interrupted; reconcile before any new attempt."}
            db.execute("INSERT INTO jobs(mode,request_key,fingerprint,state) VALUES(?,?,?,?)",
                       (self.mode, request_key, fingerprint, "submitting"))
        # Reservation commits BEFORE the network call. A crash cannot silently resend.
        # Hash tags so customer identifiers do not appear in provider reporting.
        tag = "standalone_" + hashlib.sha256(request_key.encode()).hexdigest()[:32]
        base = {"request_key": request_key, "mode": self.mode, "reference_tag": tag}
        if self.mode == "dry_run":
            return self._finish(request_key, {**base, "state": "dry_run", "cost": "0",
                                             "message": "Offline simulation; nothing sent."})
        payload = {"test": "1" if self.mode == "test" else "0",
                   "template": str(letter["template_id"]), "size": "A4",
                   "duplex": "0", "clearzone": "1", "post_unverified": "0", "tags": tag}
        payload.update({f"recipient[{k}]": v for k, v in letter["recipient"].items()})
        try:
            response = self.transport(payload, self.api_key)
            if not isinstance(response, dict) or type(response.get("success")) is not bool:
                raise ValueError("Invalid provider envelope")
            if not response["success"]:
                return self._finish(request_key, {**base, "state": "rejected",
                    "message": "Provider rejected the request. Check provider account; no automatic retry."})
            data = response.get("data")
            if not isinstance(data, dict):
                raise ValueError("Missing provider data")
            provider_id = data.get("id")
            if self.mode == "live" and (isinstance(provider_id, bool) or
                    not str(provider_id).isdigit() or int(provider_id) <= 0 or data.get("status") == "test"):
                raise ValueError("Missing live submission evidence")
            if self.mode == "test" and data.get("status") != "test":
                raise ValueError("Unexpected test response")
            result = {**base, "state": "test" if self.mode == "test" else "submitted",
                      "provider_id": provider_id, "provider_status": data.get("status"),
                      "cost": data.get("cost"), "preview_url": data.get("pdf")}
        except Exception:
            # Timeout, HTTP error, malformed body, etc. may occur AFTER acceptance.
            # Do not store response bodies or exceptions: they can contain personal data.
            result = {**base, "state": "unknown",
                      "message": "Outcome uncertain. Check provider using reference_tag; do not resend."}
        return self._finish(request_key, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["send", "status"])
    parser.add_argument("--key", required=True)
    parser.add_argument("--letter", type=Path, help="JSON file with template_id and recipient")
    parser.add_argument("--database", default="mailing.sqlite3")
    parser.add_argument("--mode", choices=["dry_run", "test", "live"], default="dry_run")
    parser.add_argument("--confirm-live", action="store_true")
    args = parser.parse_args()
    try:
        # Local status reads do not need credentials or live authorization.
        reader_mode = args.mode if args.action == "send" else "dry_run"
        mailer = Mailer(args.database, mode=reader_mode, api_key=os.getenv("STANNP_API_KEY"),
                        allow_live=args.confirm_live and os.getenv("MAILER_ALLOW_LIVE") == "YES")
        if args.action == "status":
            mailer.mode = args.mode
            result = mailer.status(args.key)
        else:
            if args.letter is None:
                raise MailerError("--letter is required for send.")
            result = mailer.send(args.key, json.loads(args.letter.read_text(encoding="utf-8")))
        print(json.dumps(result, indent=2))
        return 0 if result and result["state"] in {"dry_run", "test", "submitted"} else 2
    except (MailerError, ValueError, OSError, sqlite3.Error) as exc:
        # No traceback or provider credentials in command output.
        print(json.dumps({"error": str(exc) if isinstance(exc, MailerError) else
                          "Cannot read input/database. Check file format, path and permissions."}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
