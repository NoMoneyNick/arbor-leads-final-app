"""
worker.py -- turns letter_obligations rows into actual (or dry-run) provider
attempts. This is the piece connecting fulfilment.create_allocation_and_obligation
(which creates a 'pending_approval' / 'pending_funding' / 'blocked_missing_data'
row at sale time -- see database.py's four allocation call sites) to
letter_providers.registry.attempt_send (which sends/dry-runs a single
already-'ready' obligation -- see that function's own docstring for the
fallback/unknown-outcome rules).

Three responsibilities, kept as separate functions so each can be tested,
scheduled, and reasoned about independently:

  promote_pending_approvals -- pending_approval -> pending_funding, only
    when the CURRENT contractor_letter_settings for the obligation's buyer
    are approved AND the settings' current TEMPLATE-level fingerprint
    (letter_content.template_fingerprint -- business name/phone/notes/
    version, deliberately independent of any specific lead) matches what
    was approved (section 5: "a material change requires renewed
    approval"). Re-checked at promotion time, not trusted from obligation-
    creation time -- a contractor editing their settings after a sale but
    before their letter is promoted cannot result in stale, unapproved
    content moving forward. An obligation that cannot be positively
    confirmed (no settings saved yet, lead row missing, template
    fingerprint mismatch) is left exactly where it is; nothing here ever
    promotes on absence of information.

    2026-09-18 review, Section 1 (second pass -- "reusable-template
    approval"): this used to check the full PER-LEAD render's fingerprint
    against approved_fingerprint, which meant an approval could only ever
    match the one specific lead it happened to be computed against --
    every other/future lead has a different address/summary/council baked
    into its render, so it would never match and would stay stuck in
    pending_approval regardless of the contractor's settings being
    perfectly current. That made "reusable template approval" not
    actually reusable across leads. Fixed by checking the template-level
    fingerprint instead -- see main.py's /letter-settings/preview and
    /letter-settings/approve routes (the actual contractor-facing UI this
    enables) and tests/test_letter_settings_journey.py for the full
    customer journey this now supports end to end.

  promote_pending_funding -- pending_funding -> ready, gated by the funding
    gate (funding.py; default mode 'hold' -- see that file's docstring).
    Checked once per obligation, not once for the whole batch, so a
    partially funded budget promotes only as many obligations as it can
    actually cover rather than either over-promoting or blocking the whole
    batch on one insufficiently funded obligation.

  run_batch -- claims and attempts to send a batch of already-'ready'
    obligations via letter_providers.registry.attempt_send. This is the
    only function in this file that can result in a real provider call, and
    only when both (a) is_dry_run=False is explicitly passed, AND (b) the
    registry passed in contains a provider slot whose is_configured() is
    True. Neither is true by default in this codebase: no caller in this
    session's work passes is_dry_run=False, and none of the shipped
    provider adapters (fake/Stannp/Intelliprint/Postworks) read real
    credentials unless an operator explicitly sets them -- see
    docs/operator_guide.md.

KNOWN GAP, DELIBERATELY NOT PAPERED OVER: `leads.address` is a single
free-text field (e.g. "14 Elm Grove, Leeds, LS6 3AB"), not structured
line1/city/postcode/country components. run_batch below passes the whole
string as address_lines['line1'] and leaves city/postcode blank rather than
guessing a parse -- a real postal provider integration will likely need a
proper address-parsing step (or capturing structured fields at the planning-
data ingestion stage) before any real letter is submitted. Flagged in
docs/launch_checklist.md; not solved here.

2026-09-18 review, Section 6 (second pass -- "guarding worker functions is
not enough if nothing invokes them"): confirmed true at the time of the
first pass -- nothing anywhere in this codebase called promote_pending_
approvals/promote_pending_funding/run_batch. `run_one_pass` below is now
the single entry point that sequences all three stages, and it has two
actual callers: main.py's `/trigger-letter-fulfilment-worker` route (an
HTTP entry point an external cron service can hit, matching the exact
pattern already used for the scanning pipeline's `/trigger-*` routes) and
the standalone `worker_runner.py` script at the repo root (a local entry
point that needs no FastAPI process at all). See docs/operator_guide.md
section 5 for how each is actually started, locally and once deployed --
neither is deployed or scheduled by this session's own work; both are
inert until an operator (you) points a real scheduler at one of them.
Deliberately NOT auto-started as an in-process background thread the way
main.py's `_autonomous_scheduler_loop` starts the scanning pipeline on
every app boot -- see run_one_pass's own docstring for why.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Optional

import fulfilment
import letter_content
import funding as funding_module
from letter_providers.registry import ProviderRegistry, attempt_send, SendOutcome

logger = logging.getLogger("treekey-fulfilment-worker")

ESTIMATED_LETTER_COST_PENCE_ENV = "ESTIMATED_LETTER_COST_PENCE"
DEFAULT_ESTIMATED_LETTER_COST_PENCE = 95  # 2nd class stamp + paper, a placeholder -- see operator_guide.md


def estimated_letter_cost_pence() -> int:
    """Read fresh from the environment every call, same idiom as every
    other config flag this session added. promote_pending_funding's
    estimated_cost_pence parameter is a single flat figure applied to
    every obligation in a batch (a pre-existing simplification, not
    something this pass changes) -- this just makes that figure
    configurable instead of a hardcoded literal at each call site."""
    raw = os.getenv(ESTIMATED_LETTER_COST_PENCE_ENV, "").strip()
    if not raw:
        return DEFAULT_ESTIMATED_LETTER_COST_PENCE
    try:
        return int(raw)
    except ValueError:
        logger.warning(f"[Worker] {ESTIMATED_LETTER_COST_PENCE_ENV}={raw!r} is not an integer -- "
                        f"using default {DEFAULT_ESTIMATED_LETTER_COST_PENCE}p.")
        return DEFAULT_ESTIMATED_LETTER_COST_PENCE


@dataclass
class PromotionReport:
    checked: int = 0
    promoted_to_pending_funding: int = 0
    promoted_to_ready: int = 0
    left_pending_approval: int = 0
    left_pending_funding_ineligible: int = 0
    errors: int = 0
    refused: Optional[str] = None  # 2026-09-18 review, Section 2 -- set (non-None)
    # when this function refused to run at all because fulfilment.active_pipeline()
    # != 'fulfilment'; every other field is left at its zero default in that case.


def _refuse_unless_fulfilment_pipeline_active(caller_name: str) -> bool:
    """2026-09-18 review, Section 2: 'Ensure each new eligible allocation
    can enter only one dispatch pipeline.' database.py's _dispatch_via_
    active_pipeline already guarantees this at ALLOCATION time (which
    table a new sale's row goes into); this is the mirror-image guarantee
    at PROCESSING time -- these three functions must not act on
    letter_obligations rows unless the operator has actually switched
    LETTER_DISPATCH_PIPELINE to 'fulfilment'. Without this, an operator
    could leave the pipeline flag at its default 'legacy' (so new sales
    keep flowing into the OLD letter_dispatches queue) while still
    running worker.py's functions by hand -- which would find nothing to
    do today, but silently stop being a no-op the moment anything ever
    did write into letter_obligations under 'legacy' mode by mistake.
    Returns True if the caller should refuse (pipeline is not
    'fulfilment'), logging why."""
    import fulfilment
    pipeline = fulfilment.active_pipeline()
    if pipeline != "fulfilment":
        logger.warning(f"[Worker] {caller_name} refusing to run: LETTER_DISPATCH_PIPELINE={pipeline!r}, "
                        f"not 'fulfilment' -- database.py's four allocation call sites are not currently "
                        f"writing to letter_obligations at all in this mode, so there is nothing for "
                        f"{caller_name} to safely act on. Set LETTER_DISPATCH_PIPELINE=fulfilment to enable "
                        f"the new pipeline (see docs/launch_checklist.md item 2).")
        return True
    return False


def _fetch_lead_content_fields(cur, lead_reference: str):
    """Returns (summary, council) for re-rendering, or (None, None) if the
    lead row can't be found -- callers must treat that as 'cannot verify,
    do not promote/send', never as empty strings standing in for real data."""
    cur.execute("SELECT summary, council_source FROM leads WHERE reference = %s;", (lead_reference,))
    row = cur.fetchone()
    if not row:
        return None, None
    return row[0], row[1]


def promote_pending_approvals(cur, batch_limit: int = 100) -> PromotionReport:
    if _refuse_unless_fulfilment_pipeline_active("promote_pending_approvals"):
        return PromotionReport(refused="active_pipeline_is_not_fulfilment")
    report = PromotionReport()
    cur.execute("""
        SELECT id, lead_reference, address, applicant_name, buyer_email
        FROM letter_obligations WHERE status = 'pending_approval'
        ORDER BY created_at ASC LIMIT %s;
    """, (batch_limit,))
    rows = cur.fetchall()
    for obligation_id, lead_reference, address, applicant_name, buyer_email in rows:
        report.checked += 1
        try:
            settings = letter_content.get_contractor_settings(cur, buyer_email)
            if not settings or not settings.approved:
                report.left_pending_approval += 1
                continue
            # 2026-09-18 review, Section 1 (second pass -- "reusable-template
            # approval"): eligibility is checked against the TEMPLATE-level
            # fingerprint (business_name/phone/notes/version -- see
            # letter_content.template_fingerprint's own docstring for why),
            # never a full per-lead render's fingerprint. A full-render
            # fingerprint would be different for every lead this contractor
            # buys (different address/summary/council baked in), which would
            # make an approval matchable against only the one specific lead
            # it happened to be computed against -- defeating the entire
            # point of a REUSABLE approval that should cover every future
            # lead until the contractor's own settings actually change.
            current_template_fp = letter_content.template_fingerprint(settings)
            if not letter_content.is_approval_current(settings, current_template_fp):
                # Settings have drifted since the contractor last approved
                # (or they've never approved at all) -- correctly stays in
                # pending_approval (they need to re-preview and re-approve),
                # never silently promoted on stale consent.
                report.left_pending_approval += 1
                continue
            summary, council = _fetch_lead_content_fields(cur, lead_reference)
            if summary is None:
                report.left_pending_approval += 1
                continue
            html = letter_content.render_letter(settings, lead_reference=lead_reference,
                                                  address=address, summary=summary, council=council)
            fingerprint = letter_content.content_fingerprint(html)
            # 2026-09-18 review, Section 7: "an approved letter's content
            # stays fixed through submission and fallback, even if
            # contractor details or templates change later." Freezing
            # `html` into approved_content_html HERE -- at the moment it is
            # actually approved -- is what makes that true. Before this,
            # only the fingerprint was stored; run_batch re-rendered from
            # whatever contractor_letter_settings/lead row happened to be
            # live at SEND time, which could silently diverge from what was
            # approved if either changed in between (see run_batch's own
            # updated docstring, and tests/test_content_freezing.py).
            # 2026-09-24 handoff ("My Introductions" account view, "template/
            # version used" per-introduction field): letter_obligations.
            # template_version was defined in the schema (fulfilment.py's
            # init_fulfilment_schema) and accepted as a parameter by
            # fulfilment.create_allocation_and_obligation, but NO caller
            # anywhere in the codebase ever actually passed a value for it --
            # confirmed by grepping every caller of that function. Every
            # obligation's template_version has therefore always been NULL,
            # for the whole lifetime of this table. This is the one place a
            # specific template is actually frozen for a specific obligation
            # (settings.template_version, read from the SAME `settings` row
            # whose approved wording is being frozen into approved_content_html
            # on this exact line) -- so it is the correct, and only correct,
            # place to stamp it, not obligation-creation time (before
            # approval is even checked, the eventual template could still
            # change).
            cur.execute("""
                UPDATE letter_obligations
                SET status = 'pending_funding', content_fingerprint = %s, approved_content_html = %s,
                    template_version = %s, updated_at = NOW()
                WHERE id = %s AND status = 'pending_approval' RETURNING id;
            """, (fingerprint, html, settings.template_version, obligation_id))
            if cur.fetchone() is not None:
                report.promoted_to_pending_funding += 1
            else:
                report.left_pending_approval += 1
        except letter_content.LetterConfigError as e:
            logger.error(f"[Worker] Cannot re-render obligation {obligation_id} for approval check "
                         f"(configuration incomplete): {e}")
            report.errors += 1
    return report


def promote_pending_funding(cur, gate: funding_module.FundingGate, *, estimated_cost_pence: int,
                             batch_limit: int = 100) -> PromotionReport:
    if _refuse_unless_fulfilment_pipeline_active("promote_pending_funding"):
        return PromotionReport(refused="active_pipeline_is_not_fulfilment")
    report = PromotionReport()
    cur.execute("""
        SELECT id FROM letter_obligations WHERE status = 'pending_funding'
        ORDER BY created_at ASC LIMIT %s;
    """, (batch_limit,))
    rows = cur.fetchall()
    for row in rows:
        obligation_id = row[0]
        report.checked += 1
        decision = gate.check(cur, estimated_cost_pence=estimated_cost_pence)
        if not decision.eligible:
            report.left_pending_funding_ineligible += 1
            continue
        cur.execute("""
            UPDATE letter_obligations SET status = 'ready', updated_at = NOW()
            WHERE id = %s AND status = 'pending_funding' RETURNING id;
        """, (obligation_id,))
        if cur.fetchone() is not None:
            report.promoted_to_ready += 1
        else:
            report.left_pending_funding_ineligible += 1
    return report


def run_batch(cur, registry: ProviderRegistry, *, worker_id: str, is_dry_run: bool = True,
              batch_limit: int = 25, estimated_cost_pence: Optional[int] = None,
              gate: Optional[funding_module.FundingGate] = None) -> list:
    """Returns the list of SendOutcome results actually produced (skipped-
    before-claim cases -- unrenderable content, missing settings/lead --
    are logged loudly and excluded from the returned list rather than
    silently retried, since retrying an obligation this function couldn't
    even render would just fail identically every time).

    2026-09-18 review, Section 6: `gate` defaults to a fresh
    funding.FundingGate() (reading FUNDING_MODE from the environment,
    fail-safe default 'hold') when the caller doesn't supply one -- this is
    THE real production enforcement point for atomic funding reservation;
    see letter_providers.registry.attempt_send's own docstring for why it
    itself only warns-and-proceeds when called without a gate rather than
    refusing outright (so its own fallback-logic-focused unit tests keep
    working unchanged). Passing an explicit `gate` is how a caller shares
    one gate/mode across an entire batch run (e.g. to use 'simulate' mode
    in a test) rather than getting a fresh 'hold'-mode one per call.

    2026-09-18 review, Section 7: sends `approved_content_html` EXACTLY as
    frozen by promote_pending_approvals at approval time -- it does NOT
    call letter_content.render_letter again here. This used to re-render
    from whatever contractor_letter_settings/leads rows were live at send
    time, which meant a contractor editing their letter settings (or an
    admin editing lead content) AFTER approval but BEFORE the worker got
    around to sending could silently change what actually got posted,
    without the contractor ever re-approving the new wording -- exactly
    the gap Section 7 asked to be closed ("an approved letter's content
    stays fixed through submission and fallback, even if contractor
    details or templates change later"). An obligation that somehow
    reached 'ready' with no frozen content (a pre-existing row from before
    this column existed, or a bug elsewhere) is treated as a hard error and
    skipped -- never silently re-rendered as a fallback, which would
    quietly reopen the exact gap this closes."""
    if _refuse_unless_fulfilment_pipeline_active("run_batch"):
        return []

    if gate is None:
        gate = funding_module.FundingGate()

    cur.execute(
        "SELECT id, lead_reference, address, applicant_name, idempotency_key, approved_content_html "
        "FROM letter_obligations WHERE status = 'ready' ORDER BY created_at ASC LIMIT %s;",
        (batch_limit,),
    )
    rows = cur.fetchall()
    outcomes: list[SendOutcome] = []
    for obligation_id, lead_reference, address, applicant_name, idem_key, html in rows:
        if not html:
            logger.error(f"[Worker] Obligation {obligation_id} reached 'ready' with no frozen "
                          f"approved_content_html -- refusing to re-render or guess content; skipping "
                          f"for manual investigation (see Section 7 of the 2026-09-18 review).")
            continue

        outcome = attempt_send(
            cur, registry, obligation_id, worker_id=worker_id, content_html=html,
            # See module docstring's "KNOWN GAP" note -- address is not
            # structured at the source, so only line1 is populated here.
            address_lines={"line1": address, "city": "", "postcode": "", "country": "GB"},
            applicant_name=applicant_name, lead_reference=lead_reference, idempotency_key=idem_key,
            is_dry_run=is_dry_run, estimated_cost_pence=estimated_cost_pence, gate=gate,
        )
        outcomes.append(outcome)
    return outcomes


@dataclass
class WorkerPassReport:
    """Combined result of one run_one_pass call. pipeline_active=False
    means the whole pass was a no-op (LETTER_DISPATCH_PIPELINE wasn't
    'fulfilment') and every other field stays at its default -- callers
    should treat that as "nothing to do", not as an error."""
    pipeline_active: bool
    is_dry_run: bool = True
    approvals: Optional[PromotionReport] = None
    funding: Optional[PromotionReport] = None
    send_outcomes: list = field(default_factory=list)


def run_one_pass(cur, *, worker_id: str, is_dry_run: bool = True,
                  registry: Optional[ProviderRegistry] = None,
                  gate: Optional[funding_module.FundingGate] = None,
                  estimated_cost_pence: Optional[int] = None,
                  approval_batch_limit: int = 100, funding_batch_limit: int = 100,
                  send_batch_limit: int = 25) -> WorkerPassReport:
    """2026-09-18 review, Section 6: the single place all three stages get
    sequenced, so an actual scheduling entry point (the HTTP trigger route,
    the standalone script) has exactly one function to call rather than
    reimplementing the promote-promote-send order twice. This is a
    convenience wrapper, not a new gate: each of the three stage functions
    still refuses independently if called directly, exactly as before.

    Deliberately does NOT manage the database connection/transaction (only
    takes a caller-supplied `cur`, same as the three stage functions) and
    does NOT catch exceptions from the stages -- a real failure partway
    through should abort the whole pass and propagate to the caller, which
    owns commit/rollback (see docs/operator_guide.md section 5 for the
    exact conn.commit() placement). Swallowing a mid-pass error here would
    let the caller wrongly commit a partial pass.

    Defaults to is_dry_run=True -- a caller must explicitly pass
    is_dry_run=False to ever reach a real (non-fake, network) provider
    call, on top of that provider slot actually being configured (see
    letter_providers/registry.py). Nothing about calling this function
    itself turns real sending on.

    Deliberately NOT auto-started as an in-process background thread on
    FastAPI startup (unlike main.py's `_autonomous_scheduler_loop` for the
    scanning pipeline): switching LETTER_DISPATCH_PIPELINE to 'fulfilment'
    to test something else should never, by itself, start a background
    loop that begins attempting sends. Starting this requires a human
    explicitly pointing a real scheduler (external cron hitting the HTTP
    route, or running worker_runner.py) at it."""
    if fulfilment.active_pipeline() != "fulfilment":
        logger.warning(f"[Worker] run_one_pass: LETTER_DISPATCH_PIPELINE={fulfilment.active_pipeline()!r}, "
                        f"not 'fulfilment' -- nothing to do.")
        return WorkerPassReport(pipeline_active=False, is_dry_run=is_dry_run)

    if gate is None:
        gate = funding_module.FundingGate()
    if estimated_cost_pence is None:
        estimated_cost_pence = estimated_letter_cost_pence()
    if registry is None:
        from letter_providers.registry import build_registry_from_env
        registry = build_registry_from_env()

    report = WorkerPassReport(pipeline_active=True, is_dry_run=is_dry_run)
    report.approvals = promote_pending_approvals(cur, batch_limit=approval_batch_limit)
    report.funding = promote_pending_funding(cur, gate, estimated_cost_pence=estimated_cost_pence,
                                              batch_limit=funding_batch_limit)
    report.send_outcomes = run_batch(cur, registry, worker_id=worker_id, is_dry_run=is_dry_run,
                                      batch_limit=send_batch_limit, estimated_cost_pence=estimated_cost_pence,
                                      gate=gate)
    return report
