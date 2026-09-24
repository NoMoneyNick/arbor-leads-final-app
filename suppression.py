"""
suppression.py -- Address/person-keyed suppression for the letter pipeline.

Deliberately separate from database.email_suppressions (database.py:426),
which is email-only and belongs to the marketing/teaser-email system. This
table exists because a postal letter has no email address to key on, and
because a suppression here must never be confused with -- or silently
control -- unrelated email marketing suppression.

Checked at three points, per the brief's section 7 ("during selection,
allocation, queue creation and immediately before submission"):
  1. is_suppressed() can be called wherever a lead is being selected/priced
     for a marketplace listing (not wired into scanners.py/marketplace
     listing in this pass -- see docs/launch_checklist.md; this module
     provides the primitive, main.py's marketplace route would need one
     call added).
  2. fulfilment.create_allocation_and_obligation does NOT itself call this
     (kept decoupled -- allocation is "who bought the lead", suppression is
     "should a letter go out"), but the letter worker (letter_providers.registry)
     checks it again right before claiming a row for submission -- so a
     suppression added AFTER queueing still blocks the send. See
     test_suppression.py's test_suppression_added_after_queueing_still_blocks.
  3. Checked a third time inside attempt_send itself, immediately before the
     provider call, closing the gap where time passed between the worker's
     claim and the actual network call.

WHAT THIS FILE DOES NOT DECIDE:
It does not decide whether unsold leads are in-scope for Article 14 notices,
and it does not create any mass-mailing of unsold records. That policy
question is explicitly left open -- see docs/launch_checklist.md. This file
only provides the mechanism to honour an objection once one exists, for
whichever leads the business ends up needing to notify.

MULTIPLE APPLICATIONS AT THE SAME ADDRESS:
Suppression is keyed on a normalised address AND, where known, the
applicant's name -- not address alone. A property with multiple owners/
occupants over time (e.g. a later planning application for the same address
made by a different person) must not be silently treated as "the same
person already objected". See match logic in is_suppressed() below and its
tests.

2026-09-22 handoff: "Use a secure matching design (e.g. a keyed digest of
the normalized address/name), not an easily guessed plain address hash or
stored plaintext ... must block re-import and sending without preserving a
browsable address list." Before this, normalized_address stored the real
address as plain, directly queryable text -- anyone with read access to
this one table (or a backup/export of it) could browse every address
someone had objected to, in the clear. Matching now goes through
_address_digest/_address_person_digest -- an HMAC-SHA256 keyed digest, not
a bare hash (a bare SHA-256 of a real-world address is crackable by
dictionary/rainbow-table against the UK's public address data, which is
exactly the "easily guessed" the instruction calls out; HMAC's secret key
is what makes that infeasible). add_suppression no longer writes a real
address into normalized_address -- new rows leave it NULL. Existing rows
written before this change still have the real address sitting there in
plaintext until backfill_address_suppression_hashes is run once against
production (see that function's own docstring) -- this is a real,
documented transition gap: an old row's plaintext is only actually cleared,
and its hash-based matching only actually active, once that backfill runs.
Flagged explicitly in the gap-list report, not silently assumed done.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger("treekey-suppression")

SUPPRESSION_HASH_KEY_ENV = "SUPPRESSION_HASH_KEY"


def _hash_key() -> bytes:
    """The HMAC key behind every address/applicant-name digest this module
    stores or matches on. Read fresh from the environment on every call --
    same 'no caching, no restart needed to rotate' posture as
    address_release.address_release_live() -- so rotating a leaked key
    takes effect immediately. Rotating it DOES mean every existing row's
    stored digest stops matching (a new key produces different digests for
    the same address) -- re-run backfill_address_suppression_hashes after
    a rotation, same as after the very first deploy of this scheme.

    Deliberately fails LOUD (raises), not safe-and-silent: the fail-safe
    direction for a suppression check is to BLOCK sending, never to
    silently let one through because the key was missing -- and letting
    add_suppression silently store an unhashed/unmatchable row would be
    just as bad (an objection nobody could ever actually match against
    again). Callers (is_suppressed's own callers in the letter pipeline)
    must treat this the same as any other 'cannot confirm this address is
    safe to send to' failure -- see letter_providers/registry.py and
    worker.py's own existing fail-closed conventions for provider errors."""
    key = os.getenv(SUPPRESSION_HASH_KEY_ENV, "").strip()
    if not key:
        raise RuntimeError(
            f"{SUPPRESSION_HASH_KEY_ENV} is not configured. Set it in secure local "
            "configuration (never in chat) before using suppression.py -- see this "
            "module's own 2026-09-22 handoff note on why matching fails loud, not safe-"
            "and-silent, with no key set."
        )
    return key.encode("utf-8")


def _address_digest(normalized_address: str) -> str:
    """Keyed digest of the address alone -- the match key for
    scope='this_address_anyone' rows."""
    return hmac.new(_hash_key(), normalized_address.encode("utf-8"), hashlib.sha256).hexdigest()


def _address_person_digest(normalized_address: str, name: Optional[str]) -> str:
    """Keyed digest of address+name TOGETHER (not the two hashed
    separately and compared independently) -- the match key for
    scope='this_person' rows. Keeping it a single combined digest means a
    scope='this_person' row can only ever match the exact (address, name)
    pair it was recorded against, never accidentally via the address-only
    digest."""
    joined = f"{normalized_address}|{(name or '').strip().lower()}"
    return hmac.new(_hash_key(), joined.encode("utf-8"), hashlib.sha256).hexdigest()


def init_suppression_schema(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS postal_suppressions (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            normalized_address TEXT,         -- 2026-09-22 handoff: no longer written by
            -- add_suppression (kept nullable, not dropped, only for rows written before
            -- this change -- see backfill_address_suppression_hashes). A new row leaves
            -- this NULL; matching never reads it.
            address_hash TEXT,               -- HMAC-SHA256(key, normalized_address) -- see _address_digest
            address_person_hash TEXT,        -- HMAC-SHA256(key, normalized_address+applicant_name) -- see _address_person_digest
            applicant_name TEXT,             -- NULL = applies to the address regardless of named person
            reason TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT 'this_person',  -- 'this_person' | 'this_address_anyone'
            recorded_by TEXT NOT NULL,
            source_contact TEXT,             -- how the objection arrived (email address, phone note, etc.) -- never logged elsewhere
            created_at TIMESTAMPTZ DEFAULT NOW(),
            resolved BOOLEAN NOT NULL DEFAULT FALSE,
            resolved_by TEXT,
            resolved_at TIMESTAMPTZ,
            resolution_note TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_postal_suppressions_address_hash ON postal_suppressions(address_hash) WHERE resolved = FALSE;
        CREATE INDEX IF NOT EXISTS idx_postal_suppressions_address_person_hash ON postal_suppressions(address_person_hash) WHERE resolved = FALSE;

        -- 2026-09-22 handoff migration path: on a database where this table
        -- ALREADY exists (created under the old schema, normalized_address
        -- TEXT NOT NULL, no hash columns), the CREATE TABLE above is a
        -- no-op -- these statements are what actually bring an existing
        -- table up to the new shape. All idempotent/safe to run every time
        -- init_suppression_schema runs, same convention as every other
        -- schema function in this codebase.
        ALTER TABLE postal_suppressions ALTER COLUMN normalized_address DROP NOT NULL;
        ALTER TABLE postal_suppressions ADD COLUMN IF NOT EXISTS address_hash TEXT;
        ALTER TABLE postal_suppressions ADD COLUMN IF NOT EXISTS address_person_hash TEXT;
    """)


def normalize_address(address: str) -> str:
    """Lowercase, collapse whitespace/punctuation -- good enough to catch
    the common formatting variants of the same address without a full
    postal-address-parsing dependency. Deliberately conservative: this is
    used to WIDEN a match (catch near-duplicates), never to narrow one, so
    a false positive here means 'checked an extra suppression row', not
    'missed a real suppression'."""
    if not address:
        return ""
    a = address.lower().strip()
    a = re.sub(r"[,\.]", " ", a)
    a = re.sub(r"\s+", " ", a)
    return a.strip()


@dataclass
class SuppressionMatch:
    suppressed: bool
    reason: Optional[str] = None
    scope: Optional[str] = None
    row_id: Optional[str] = None


def add_suppression(cur, *, address: str, applicant_name: Optional[str], reason: str,
                     scope: str, recorded_by: str, source_contact: Optional[str] = None) -> str:
    if scope not in ("this_person", "this_address_anyone"):
        raise ValueError("scope must be 'this_person' or 'this_address_anyone'.")
    norm = normalize_address(address)
    name = (applicant_name or "").strip().lower() or None
    # 2026-09-22 handoff: normalized_address is deliberately NOT written any
    # more (NULL) -- only the keyed digests are stored. See this module's
    # own docstring for why a plain, directly-queryable address column is
    # exactly the "browsable address list" the handoff prohibits.
    cur.execute("""
        INSERT INTO postal_suppressions (normalized_address, address_hash, address_person_hash,
                                          applicant_name, reason, scope, recorded_by, source_contact)
        VALUES (NULL, %s, %s, %s, %s, %s, %s, %s) RETURNING id;
    """, (_address_digest(norm), _address_person_digest(norm, name), name, reason, scope,
          recorded_by, source_contact))
    row_id = str(cur.fetchone()[0])
    logger.info(f"[Suppression] Added by {recorded_by}: scope={scope} -> {row_id}")
    return row_id


def resolve_suppression(cur, row_id: str, *, resolved_by: str, note: str) -> bool:
    cur.execute("""
        UPDATE postal_suppressions SET resolved = TRUE, resolved_by = %s, resolved_at = NOW(), resolution_note = %s
        WHERE id = %s AND resolved = FALSE RETURNING id;
    """, (resolved_by, note, row_id))
    return cur.fetchone() is not None


def is_suppressed(cur, *, address: str, applicant_name: Optional[str]) -> SuppressionMatch:
    """Two independent match rules, both active:
      - scope='this_address_anyone' rows match on address alone (the
        occupant asked TreeKey to leave the property alone regardless of
        who the applicant is).
      - scope='this_person' rows match on address AND applicant_name (a
        named person objected; a different person later applying at the
        same address is NOT suppressed by this row).

    2026-09-22 handoff: matches via the keyed digests (_address_digest /
    _address_person_digest), never against normalized_address -- a row
    written before this change, whose real address still sits in
    normalized_address and whose hash columns are still NULL, will NOT
    match here until backfill_address_suppression_hashes has been run
    against it. This is a real, deliberate transition trade-off (matching
    plaintext would defeat the entire point of this fix), not an
    oversight -- see that function's own docstring and the gap-list report
    for why it needs to run once, promptly, after this deploys."""
    norm = normalize_address(address)
    name = (applicant_name or "").strip().lower() or None
    addr_digest = _address_digest(norm)
    addr_person_digest = _address_person_digest(norm, name)

    cur.execute("""
        SELECT id, reason, scope FROM postal_suppressions
        WHERE resolved = FALSE
          AND (
            (scope = 'this_address_anyone' AND address_hash = %s)
            OR (scope = 'this_person' AND address_person_hash = %s)
          )
        LIMIT 1;
    """, (addr_digest, addr_person_digest))
    row = cur.fetchone()
    if row:
        return SuppressionMatch(suppressed=True, reason=row[1], scope=row[2], row_id=str(row[0]))
    return SuppressionMatch(suppressed=False)


def backfill_address_suppression_hashes(cur, batch_limit: int = 500) -> dict:
    """One-time (repeatable/idempotent, safe to re-run) migration helper:
    finds rows still carrying a plaintext normalized_address with no hash
    computed yet (address_hash IS NULL -- written before this handoff's
    fix, or by a caller that bypassed add_suppression), computes both
    keyed digests from that plaintext, stores them, and then CLEARS the
    plaintext column -- completing, for that row, the "no browsable
    address list" requirement this whole change exists for. Must be run
    once against production shortly after this deploys (see suppression.
    py's own module docstring: until it runs, an old row's suppression
    silently cannot match a new is_suppressed() call, AND its address
    still sits there in the clear) -- not wired into any automatic
    startup/cron path, since running a bulk UPDATE like this
    unattended, on a first deploy, without an operator watching the
    count it reports, is exactly the kind of one-off migration this
    codebase's own conventions (see fulfilment.py/address_release.py's own
    schema-change comments) keep deliberately manual. Returns
    {"backfilled": int, "remaining": int} so a caller (an admin route, or
    a one-off script) knows whether to run it again for the next batch."""
    cur.execute("""
        SELECT id, normalized_address, applicant_name FROM postal_suppressions
        WHERE address_hash IS NULL AND normalized_address IS NOT NULL
        LIMIT %s;
    """, (batch_limit,))
    rows = cur.fetchall()
    backfilled = 0
    for row_id, normalized_address, applicant_name in rows:
        norm = normalize_address(normalized_address)
        name = (applicant_name or "").strip().lower() or None
        cur.execute("""
            UPDATE postal_suppressions
            SET address_hash = %s, address_person_hash = %s, normalized_address = NULL
            WHERE id = %s;
        """, (_address_digest(norm), _address_person_digest(norm, name), row_id))
        backfilled += 1
    cur.execute("SELECT count(*) FROM postal_suppressions WHERE address_hash IS NULL AND normalized_address IS NOT NULL;")
    remaining = cur.fetchone()[0]
    if backfilled:
        logger.info(f"[Suppression] Backfilled {backfilled} row(s) to keyed digests ({remaining} remaining).")
    return {"backfilled": backfilled, "remaining": remaining}
