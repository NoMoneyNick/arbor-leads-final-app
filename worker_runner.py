#!/usr/bin/env python3
"""
worker_runner.py -- 2026-09-18 review, Section 6: THE local entry point for
the letter-fulfilment worker (worker.run_one_pass), needing no FastAPI
process at all. The deployed entry point is main.py's
/trigger-letter-fulfilment-worker route (an external cron hits it) -- see
docs/operator_guide.md section 5 for both, and for exactly why NEITHER is
actually scheduled anywhere by this session's own work. Running this script
does not deploy or schedule anything by itself; it runs one pass (or a
local sleep-loop of passes) against whatever DATABASE_URL/env this shell
already has configured, then exits.

Usage:
    python3 worker_runner.py                  # one pass, dry-run (default, safe)
    python3 worker_runner.py --live            # one pass, allows a real provider
                                                # call IF a provider slot is also
                                                # configured (see registry.py --
                                                # this flag alone sends nothing)
    python3 worker_runner.py --loop            # repeat every --interval seconds
                                                # until Ctrl-C, dry-run
    python3 worker_runner.py --loop --interval 300 --live

Every run is a single pass through worker.run_one_pass -- see that
function's own docstring for the promote/promote/send sequencing and why
this file does not catch a mid-pass exception (it lets the pass fail and
the transaction roll back, then either exits (single-run mode) or logs and
waits for the next tick (--loop mode), rather than committing a partial
pass).

This script refuses to do anything (a clean, logged no-op) unless
LETTER_DISPATCH_PIPELINE=fulfilment is set in the environment -- same
guarantee every other entry point to this pipeline already has (see
worker.py's _refuse_unless_fulfilment_pipeline_active).
"""
from __future__ import annotations

import argparse
import logging
import sys
import time

logger = logging.getLogger("treekey-fulfilment-worker-runner")


def run_once(*, is_dry_run: bool, worker_id: str) -> int:
    """Runs a single pass. Returns a process-exit-code-shaped int: 0 on a
    clean pass (including a pipeline_active=False no-op), 1 on an
    exception. Imports database/worker/fulfilment lazily so `--help`
    works even without DATABASE_URL/psycopg2 configured."""
    import database
    import worker
    import fulfilment

    logger.info(f"[WorkerRunner] Starting one pass (pipeline={fulfilment.active_pipeline()!r}, "
                f"is_dry_run={is_dry_run}).")
    conn = database.get_db_conn()
    cur = conn.cursor()
    try:
        report = worker.run_one_pass(cur, worker_id=worker_id, is_dry_run=is_dry_run)
        conn.commit()
    except Exception:
        conn.rollback()
        logger.error("[WorkerRunner] Pass failed, rolled back.", exc_info=True)
        return 1
    finally:
        cur.close()
        conn.close()

    if not report.pipeline_active:
        logger.info("[WorkerRunner] No-op: LETTER_DISPATCH_PIPELINE is not 'fulfilment'.")
        return 0
    logger.info(f"[WorkerRunner] Pass complete. approvals={report.approvals} funding={report.funding} "
                f"send_attempts={len(report.send_outcomes)}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the letter-fulfilment worker locally (no FastAPI process needed).")
    parser.add_argument("--live", action="store_true",
                         help="Allow a real (non-dry-run) provider call. Still sends nothing unless a "
                              "provider slot is also configured (LETTER_PROVIDER_PRIMARY etc. -- see "
                              "letter_providers/registry.py). Default: dry-run.")
    parser.add_argument("--loop", action="store_true",
                         help="Repeat every --interval seconds until interrupted (Ctrl-C). Default: run once and exit.")
    parser.add_argument("--interval", type=int, default=900,
                         help="Seconds between passes in --loop mode. Default: 900 (15 minutes).")
    parser.add_argument("--worker-id", default="worker_runner_local",
                         help="Identifier recorded against attempts (see letter_providers/registry.py's "
                              "worker_id-keyed idempotency checks). Default: worker_runner_local.")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if not args.loop:
        return run_once(is_dry_run=not args.live, worker_id=args.worker_id)

    logger.info(f"[WorkerRunner] Loop mode: one pass every {args.interval}s, is_dry_run={not args.live}. Ctrl-C to stop.")
    try:
        while True:
            run_once(is_dry_run=not args.live, worker_id=args.worker_id)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        logger.info("[WorkerRunner] Stopped (Ctrl-C).")
        return 0


if __name__ == "__main__":
    sys.exit(main())
