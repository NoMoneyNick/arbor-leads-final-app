"""
letter_content.py -- The ONE letter-rendering function, used by both the
contractor-facing preview and the provider submission path. Extracted from
main.py's generate_homeowner_letter (main.py:3225-3340 in the inspected
working copy), which is kept as a thin wrapper calling into here (see the
main.py diff) so the preview a contractor sees is provably the same content
that gets submitted -- not a second, potentially-diverging copy.

CONFIGURATION, NOT GUESSING (section 5): the previous version of this letter
had two different privacy-contact addresses in two different places --
main.py's letter footer said contact@treekey.co.uk, privacy_policy_draft.md
said contact@treekey.uk. This file reads ONE value,
TREEKEY_PRIVACY_CONTACT_EMAIL, and uses it everywhere the letter needs a
privacy contact. If that env var is unset, render_letter() raises rather
than guessing -- see PRIVACY_CONTACT_EMAIL_ENV below. Set it once the
correct operational address is confirmed; do not treat either historical
value as authoritative without checking which inbox is actually read.

WHAT THIS FILE DOES NOT CLAIM:
render_letter() produces a letter that INCLUDES a privacy notice section.
It does not claim, anywhere, that sending it establishes GDPR compliance --
see the "legal_status_note" constant, which is deliberately descriptive
("this letter includes..."), not a compliance assertion. Whether the
combined letter + linked privacy policy actually satisfies Article 14, and
whether the timing of disclosure is compliant, remains explicitly
unresolved -- see the corrected assessment from earlier this session and
docs/launch_checklist.md.

=============================================================================
2026-09-23 handoff -- CONTRACTOR TEMPLATE SELECTOR + CONSTRAINED EDITOR
=============================================================================
Adds: (a) a choice of THREE versioned template "shells" (TEMPLATE_REGISTRY
below -- Friendly Introduction / Professional and Factual / Short and
Direct), (b) three new contractor-editable fields (business_intro,
services_note, contact_email) alongside the existing ones, all with
per-field length limits and a combined content budget, and (c) explicit
input hardening (no raw HTML/script characters accepted; every dynamic
value is HTML-escaped at render time regardless).

WHAT STAYS LOCKED, AND WHY IT STAYS SAFE WITHOUT A UI-LEVEL CHECK: the
privacy/data-source/"how we found your details"/contact-policy section at
the bottom of render_letter() is built ENTIRELY from server-side
configuration (_privacy_contact_email(), _privacy_policy_url()) and fixed
copy -- there is no field, form name, or settings column a contractor (or
an attacker posting arbitrary form data) can submit that reaches that
section. That is what "enforced on the server, not just in the interface"
means here: it isn't a permissions check on an editable field, it's that
the field to tamper with does not exist on the server side at all. Template
selection (TEMPLATE_REGISTRY) only ever substitutes the OPENING line, the
QUOTE-REQUEST line, and the SIGN-OFF word -- never the footer.

PLACEHOLDER COPY, DELIBERATELY: Nick is developing the final letter wording
and PDF layout separately (see the 2026-09-22 handoff brief, section on
letter design/copy). The three template bodies below are neutral,
factual-tone placeholders so the selector/editor/approval workflow is fully
buildable and testable now, without waiting on final copy. Swapping in the
real wording later means editing the strings in TEMPLATE_REGISTRY (and
bumping that template's `version`, which changes its fingerprint and
correctly forces re-approval) -- it never requires touching render_letter,
the approval/fingerprint machinery, or the contractor-facing routes.

NO AUTOMATIC CLAIMS: none of the three template bodies contain "trusted",
"vetted", "qualified", "insured", "free"/"complimentary", or an
availability claim -- see BANNED_AUTO_CLAIM_WORDS and
tests/test_letter_content.py's TestNoAutoAddedClaimWords, which scans the
registry (and a letter rendered with every optional field blank) for them.
Credentials/insurance/qualifications remain exactly what they always were
in this file: freeform, contractor-supplied, shown verbatim or not shown at
all -- TreeKey never invents or upgrades a claim on a contractor's behalf,
and the contractor's own approval (the fingerprint mechanism below) is what
stands in for "evidence/approval" for those specific claims in this pass;
there is no document-upload/verification workflow in this codebase and this
change does not invent one.
"""
from __future__ import annotations

import hashlib
import html
import os
import re
from dataclasses import dataclass
from typing import Optional

PRIVACY_CONTACT_EMAIL_ENV = "TREEKEY_PRIVACY_CONTACT_EMAIL"
PRIVACY_POLICY_URL_ENV = "TREEKEY_PRIVACY_POLICY_URL"


class LetterConfigError(RuntimeError):
    """Raised when required, non-guessable configuration is missing.
    Deliberately loud -- 'missing configuration must fail safely and
    visibly' (brief, Authorisation and boundaries)."""


# ---------------------------------------------------------------------------
# Template registry -- versioned, replaceable shells (2026-09-23 handoff)
# ---------------------------------------------------------------------------
# Each template only supplies the OPENING line (after "Dear Homeowner,"),
# the QUOTE-REQUEST line, and the SIGN-OFF word. Everything else a
# contractor can edit (business intro, services, insurance/qualifications,
# contact details) is inserted the same way regardless of which template is
# selected, and the locked footer never varies by template at all -- see
# this module's docstring.
#
# `version` is part of what gets frozen into a contractor's approval
# fingerprint (via template_version in ContractorLetterSettings) only
# indirectly -- template_key itself is now part of template_fingerprint's
# material (see below), so switching template OR this registry's own
# `version` marker changing is not what invalidates approval; saving any
# change to settings (including a new template_key) already unconditionally
# resets approved=FALSE (upsert_contractor_settings). `version` here exists
# so a future content swap (replacing PLACEHOLDER copy with Nick's final
# wording) can be tracked/audited per template independently of any one
# contractor's own settings row.

DEFAULT_TEMPLATE_KEY = "friendly_introduction"


@dataclass(frozen=True)
class LetterTemplateDefinition:
    key: str
    label: str
    version: int
    opening_line: str          # may reference {council} and {lead_reference}
    quote_request_line: str
    sign_off_word: str


TEMPLATE_REGISTRY: "dict[str, LetterTemplateDefinition]" = {
    "friendly_introduction": LetterTemplateDefinition(
        key="friendly_introduction", label="Friendly Introduction", version=1,
        opening_line=(
            "We noticed your recent planning notice registered with {council} "
            "(Application Reference: {lead_reference}), and wanted to introduce ourselves as a "
            "local tree care business working in your area."
        ),
        quote_request_line="We would be glad to visit and talk through your plans, at a time that suits you.",
        sign_off_word="Kind regards",
    ),
    "professional_and_factual": LetterTemplateDefinition(
        key="professional_and_factual", label="Professional and Factual", version=1,
        opening_line=(
            "This letter concerns your statutory planning notification registered with {council} "
            "(Application Reference: {lead_reference}). We are contacting you as a local "
            "arboricultural contractor."
        ),
        quote_request_line=(
            "Should you wish to discuss the proposed works, we are able to arrange a site visit "
            "and provide a written quotation."
        ),
        sign_off_word="Yours sincerely",
    ),
    "short_and_direct": LetterTemplateDefinition(
        key="short_and_direct", label="Short and Direct", version=1,
        opening_line=(
            "Re: planning application {lead_reference} ({council}). We carry out tree work in "
            "your area and wanted to make contact."
        ),
        quote_request_line="Get in touch if you would like a quotation.",
        sign_off_word="Regards",
    ),
}

# TreeKey's own copy (the three templates above) must never silently assert
# any of these -- see this module's docstring, "NO AUTOMATIC CLAIMS", and
# tests/test_letter_content.py's TestNoAutoAddedClaimWords, which scans
# TEMPLATE_REGISTRY plus a fully-blank-optional-fields render against this
# list. This is not a filter applied to what a contractor types (a
# contractor's own insurance_note/qualifications_note remain their own
# factual claim, shown verbatim per this file's long-standing "never
# invented" rule) -- it is a guard on content TreeKey itself writes.
BANNED_AUTO_CLAIM_WORDS = (
    "trusted", "vetted", "qualified", "insured", "free", "complimentary",
    "available now", "currently available", "available today",
)


def template_choices() -> "list[tuple[str, str]]":
    """(key, label) pairs in a stable order, for populating a selector."""
    return [(t.key, t.label) for t in TEMPLATE_REGISTRY.values()]


# ---------------------------------------------------------------------------
# Length limits -- per-field, plus a combined "content budget"
# ---------------------------------------------------------------------------
# HEURISTIC, PENDING REAL LAYOUT TESTING: the final PDF layout is being
# developed separately with Nick (see this module's docstring), so there is
# no real paginated renderer to test against yet. These limits are a
# deliberately conservative stand-in guard -- generous enough for genuine
# content, small enough that the existing single-page HTML layout (see
# render_letter) cannot plausibly overflow onto a second page or have
# sections overlap -- so "content cannot overlap, disappear or spill into
# extra pages unnoticed" is enforced today, not left unenforced until the
# real layout exists. Revisit these numbers once Nick's PDF layout is
# final; nothing else about this mechanism needs to change to do that.
MAX_BUSINESS_NAME_LEN = 120
MAX_PHONE_LEN = 40
MAX_CONTACT_EMAIL_LEN = 254
MAX_SERVICE_AREA_LEN = 160
MAX_BUSINESS_INTRO_LEN = 500
MAX_SERVICES_NOTE_LEN = 300
MAX_INSURANCE_LEN = 300
MAX_QUALIFICATIONS_LEN = 300
# Combined cap across every editable field, independent of the per-field
# caps above (a contractor could otherwise max out every field individually
# and still produce an overlong letter).
MAX_TOTAL_DYNAMIC_CONTENT_LEN = 1400

# Reject raw markup outright rather than only relying on escaping at render
# time (belt and braces -- see render_letter's own escaping, which still
# applies unconditionally to every dynamic value including ones that reach
# it through a path other than validate(), such as the lead's own
# summary/council/address).
_DISALLOWED_MARKUP_PATTERN = re.compile(r"[<>]")


def init_letter_content_schema(cur) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS contractor_letter_settings (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            contractor_email TEXT NOT NULL UNIQUE,
            business_name TEXT NOT NULL,
            phone TEXT NOT NULL,
            service_area_note TEXT,
            insurance_note TEXT,             -- freeform, contractor-supplied; never invented by TreeKey
            qualifications_note TEXT,        -- freeform, contractor-supplied; never invented by TreeKey
            template_version INT NOT NULL DEFAULT 1,
            template_key TEXT NOT NULL DEFAULT 'friendly_introduction',
            business_intro TEXT,             -- freeform, contractor-supplied
            services_note TEXT,              -- freeform, contractor-supplied ("relevant services")
            contact_email TEXT,
            approved BOOLEAN NOT NULL DEFAULT FALSE,
            approved_fingerprint TEXT,
            approved_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        );
        -- 2026-09-23 handoff, template selector + editor: a deployment
        -- that created this table before these columns existed gets them
        -- added idempotently rather than requiring a DROP/recreate --
        -- same pattern already used by suppression.py's
        -- init_suppression_schema for its own backward-compatible
        -- migration.
        ALTER TABLE contractor_letter_settings ADD COLUMN IF NOT EXISTS template_key TEXT NOT NULL DEFAULT 'friendly_introduction';
        ALTER TABLE contractor_letter_settings ADD COLUMN IF NOT EXISTS business_intro TEXT;
        ALTER TABLE contractor_letter_settings ADD COLUMN IF NOT EXISTS services_note TEXT;
        ALTER TABLE contractor_letter_settings ADD COLUMN IF NOT EXISTS contact_email TEXT;
    """)


@dataclass
class ContractorLetterSettings:
    contractor_email: str
    business_name: str
    phone: str
    service_area_note: str = ""
    insurance_note: str = ""
    qualifications_note: str = ""
    template_version: int = 1
    approved: bool = False
    approved_fingerprint: Optional[str] = None
    # 2026-09-23 handoff -- appended at the end, all with defaults, so
    # every existing positional/keyword caller (tests included) keeps
    # working unchanged.
    template_key: str = DEFAULT_TEMPLATE_KEY
    business_intro: str = ""
    services_note: str = ""
    contact_email: str = ""

    def validate(self) -> list[str]:
        """Returns a list of problems (empty = valid). Does not invent
        anything -- a contractor who hasn't supplied insurance/qualification
        details simply gets a letter without those claims, never a
        TreeKey-fabricated one (section 5: 'Do not invent qualifications,
        insurance, approvals or testimonials').

        2026-09-23 handoff: also enforces (a) template_key is one of the
        registered templates, (b) every editable field's own length limit,
        (c) a combined content budget across all of them, and (d) no raw
        '<'/'>' characters -- 'reject arbitrary HTML or scripts', not just
        escape it (render_letter escapes everything anyway, defense in
        depth, but a contractor should see a clear validation error rather
        than have their attempted markup silently neutralised)."""
        problems = []
        if not self.business_name or not self.business_name.strip():
            problems.append("business_name is required")
        if not self.phone or not self.phone.strip():
            problems.append("phone is required")
        if self.template_key not in TEMPLATE_REGISTRY:
            problems.append(
                f"template_key must be one of {sorted(TEMPLATE_REGISTRY.keys())}, got {self.template_key!r}"
            )

        length_limited_fields = (
            ("business_name", self.business_name, MAX_BUSINESS_NAME_LEN),
            ("phone", self.phone, MAX_PHONE_LEN),
            ("contact_email", self.contact_email, MAX_CONTACT_EMAIL_LEN),
            ("service_area_note", self.service_area_note, MAX_SERVICE_AREA_LEN),
            ("business_intro", self.business_intro, MAX_BUSINESS_INTRO_LEN),
            ("services_note", self.services_note, MAX_SERVICES_NOTE_LEN),
            ("insurance_note", self.insurance_note, MAX_INSURANCE_LEN),
            ("qualifications_note", self.qualifications_note, MAX_QUALIFICATIONS_LEN),
        )
        total_len = 0
        for field_name, raw_value, max_len in length_limited_fields:
            value = raw_value or ""
            total_len += len(value)
            if len(value) > max_len:
                problems.append(
                    f"{field_name} must be {max_len} characters or fewer (currently {len(value)})"
                )
            if _DISALLOWED_MARKUP_PATTERN.search(value):
                problems.append(
                    f"{field_name} must not contain '<' or '>' -- HTML or script tags are not allowed"
                )

        if total_len > MAX_TOTAL_DYNAMIC_CONTENT_LEN:
            problems.append(
                f"combined length of your editable fields ({total_len} characters) exceeds the "
                f"{MAX_TOTAL_DYNAMIC_CONTENT_LEN}-character limit -- shorten one or more fields so "
                f"the letter cannot overflow onto an extra page"
            )

        contact_email = (self.contact_email or "").strip()
        if contact_email and "@" not in contact_email:
            problems.append("contact_email must be a valid email address")

        return problems


def get_contractor_settings(cur, contractor_email: str) -> Optional[ContractorLetterSettings]:
    cur.execute("""
        SELECT contractor_email, business_name, phone, service_area_note, insurance_note,
               qualifications_note, template_version, approved, approved_fingerprint,
               template_key, business_intro, services_note, contact_email
        FROM contractor_letter_settings WHERE contractor_email = %s;
    """, (contractor_email.strip().lower(),))
    row = cur.fetchone()
    if not row:
        return None
    return ContractorLetterSettings(
        contractor_email=row[0], business_name=row[1], phone=row[2],
        service_area_note=row[3] or "", insurance_note=row[4] or "", qualifications_note=row[5] or "",
        template_version=row[6], approved=row[7], approved_fingerprint=row[8],
        template_key=row[9] or DEFAULT_TEMPLATE_KEY, business_intro=row[10] or "",
        services_note=row[11] or "", contact_email=row[12] or "",
    )


def upsert_contractor_settings(cur, settings: ContractorLetterSettings) -> None:
    """Saving new/changed details always resets approved=FALSE -- a material
    change requires renewed approval (section 5). Approval itself is a
    separate call (approve_template) so a contractor can't approve-by-accident
    while just saving a draft.

    2026-09-23 handoff: this now also carries template_key/business_intro/
    services_note/contact_email through the same INSERT ... ON CONFLICT
    upsert as every other editable field -- changing any one of them (which
    includes switching templates) goes through this exact path and is
    therefore unconditionally subject to the same 'approved = FALSE' reset
    as a business-name or phone edit."""
    problems = settings.validate()
    if problems:
        raise ValueError(f"Invalid contractor letter settings: {'; '.join(problems)}")
    cur.execute("""
        INSERT INTO contractor_letter_settings
            (contractor_email, business_name, phone, service_area_note, insurance_note, qualifications_note,
             template_key, business_intro, services_note, contact_email, template_version, approved, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, FALSE, NOW())
        ON CONFLICT (contractor_email) DO UPDATE SET
            business_name = EXCLUDED.business_name,
            phone = EXCLUDED.phone,
            service_area_note = EXCLUDED.service_area_note,
            insurance_note = EXCLUDED.insurance_note,
            qualifications_note = EXCLUDED.qualifications_note,
            template_key = EXCLUDED.template_key,
            business_intro = EXCLUDED.business_intro,
            services_note = EXCLUDED.services_note,
            contact_email = EXCLUDED.contact_email,
            template_version = contractor_letter_settings.template_version + 1,
            approved = FALSE,
            approved_fingerprint = NULL,
            approved_at = NULL,
            updated_at = NOW();
    """, (settings.contractor_email.strip().lower(), settings.business_name.strip(), settings.phone.strip(),
          settings.service_area_note.strip(), settings.insurance_note.strip(), settings.qualifications_note.strip(),
          settings.template_key.strip(), settings.business_intro.strip(), settings.services_note.strip(),
          settings.contact_email.strip(), settings.template_version))


def template_fingerprint(settings: ContractorLetterSettings) -> str:
    """2026-09-18 review, Section 1 (second pass -- 'reusable-template
    approval'): fingerprints ONLY the fields the contractor actually
    controls (business name, phone, service-area/insurance/qualifications
    notes, template version) -- never lead_reference/address/summary/
    council, which render_letter also bakes into the full letter HTML and
    which are different for every single lead a contractor buys.

    This is deliberately NOT the same thing as content_fingerprint(html)
    of a full render. If approval matching used the full-render
    fingerprint, an approval could only ever match the ONE specific lead
    it happened to be computed against -- making 'approve once, reuse for
    every future lead' (the whole point of a reusable template) impossible
    in practice: every subsequent obligation, having a different address/
    summary/council baked into its render, would produce a different
    fingerprint and stay stuck in pending_approval forever, regardless of
    whether the contractor's own details had changed at all. See
    worker.promote_pending_approvals, which checks eligibility against
    THIS fingerprint (while still separately rendering + freezing the real
    per-lead HTML via content_fingerprint for the actual obligation, an
    entirely separate guarantee -- see that function's own comments and
    tests/test_content_freezing.py).

    2026-09-23 handoff: now also covers template_key/business_intro/
    services_note/contact_email -- switching template, or editing any of
    the three new fields, changes this fingerprint exactly like editing
    business_name always has, which is what makes 'any material change to
    wording, contact details or applicable locked text must invalidate
    reusable approval' true for the new fields too (in practice this is
    already guaranteed one layer up, by upsert_contractor_settings always
    resetting approved=FALSE on ANY save -- this fingerprint is the second,
    independent check worker.promote_pending_approvals re-verifies at
    promotion time, per that function's own docstring).

    IMPORTANT, and easy to miss: this ALSO bakes in the selected template's
    own shell text (opening/quote-request/sign-off) and its registry
    `version` marker -- not just its key. Nick's wording in
    TEMPLATE_REGISTRY is explicitly a PLACEHOLDER pending his final copy
    (see this module's docstring); when that copy is swapped in later, a
    contractor's existing approval must NOT silently start rendering the
    new wording under an old approval -- 'a material change to wording ...
    must invalidate reusable approval' applies to TreeKey's own template
    wording changing just as much as to a contractor's fields changing.
    Fingerprinting the actual shell strings (not just template_key, which
    would stay the same across a copy edit) is what makes that automatic:
    editing TEMPLATE_REGISTRY's placeholder text for a template changes
    this fingerprint for every contractor on that template, without
    needing anyone to remember to also bump every affected contractor's
    template_version by hand."""
    template = TEMPLATE_REGISTRY.get(settings.template_key) or TEMPLATE_REGISTRY[DEFAULT_TEMPLATE_KEY]
    material = "\x1f".join([
        settings.business_name.strip(), settings.phone.strip(), settings.contact_email.strip(),
        settings.template_key.strip(), str(template.version),
        template.opening_line, template.quote_request_line, template.sign_off_word,
        settings.business_intro.strip(), settings.services_note.strip(),
        settings.service_area_note.strip(), settings.insurance_note.strip(),
        settings.qualifications_note.strip(), str(settings.template_version),
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


# Fixed, clearly-fictional sample lead data used ONLY for the contractor-
# facing template preview/approval UI (main.py's /letter-settings/preview
# and /letter-settings/approve routes) -- contractor_letter_settings is
# per-contractor, not per-lead, so there is no real specific lead to show
# at setup time. Approving this preview approves the REUSABLE parts of the
# letter (see template_fingerprint above); a real purchased lead's actual
# letter is rendered separately, per-obligation, by
# worker.promote_pending_approvals using the real lead's own data.
#
# 2026-09-23 handoff: render_preview_letter (below) is the ONLY function
# that ever calls render_letter with these constants -- there is no route,
# form field, or code path anywhere that lets a contractor (or an
# attacker) supply their own lead_reference/address/summary/council into a
# preview. That is what "contractors must never receive actual homeowner
# identifiers through previews" means in practice here: not a check that
# runs and rejects real data, but the absence of any way to submit it.
PREVIEW_LEAD_REFERENCE = "SAMPLE-PREVIEW"
PREVIEW_ADDRESS = "123 Sample Street, Sample Town, ST1 2AB"
PREVIEW_SUMMARY = "Fell one silver birch and crown-reduce one oak (illustrative example only -- not a real planning application)"
PREVIEW_COUNCIL = "Sample District Council"


def render_preview_letter(settings: ContractorLetterSettings) -> str:
    """The exact same render_letter function a real send uses, against the
    fixed sample data above -- so what a contractor approves here is
    provably rendered by the same code path as a real letter, not a
    second, potentially-diverging preview template. Also, therefore, the
    same code path that fulfils "generate a preview ... through the same
    rendering path used for fulfilment" (2026-09-23 handoff) -- there is
    no separate preview renderer to keep in sync."""
    return render_letter(settings, lead_reference=PREVIEW_LEAD_REFERENCE, address=PREVIEW_ADDRESS,
                          summary=PREVIEW_SUMMARY, council=PREVIEW_COUNCIL)


def approve_template(cur, contractor_email: str, *, preview_fingerprint: str) -> None:
    """The contractor (or, in a test, whoever is asserting approval)
    confirms the exact previewed content by its fingerprint. Storing the
    fingerprint (not just an 'approved' boolean) is what lets later code
    detect drift: if the rendered content ever changes without a fresh
    approval, approved_fingerprint will no longer match, and
    obligation creation must fall back to pending_approval (see
    fulfilment.create_allocation_and_obligation's template_approved param,
    which callers compute via is_approval_current below).

    CALLER MUST PASS template_fingerprint(settings) here (computed
    server-side, from the settings row actually stored under
    contractor_email -- never a client-supplied value; see main.py's
    /letter-settings/approve route and the 'CAUTION FOR A FUTURE
    CONTRACTOR-APPROVAL UI' note in address_release.py's module
    docstring, which this route follows)."""
    cur.execute("""
        UPDATE contractor_letter_settings
        SET approved = TRUE, approved_fingerprint = %s, approved_at = NOW(), updated_at = NOW()
        WHERE contractor_email = %s RETURNING id;
    """, (preview_fingerprint, contractor_email.strip().lower()))
    if cur.fetchone() is None:
        raise LetterConfigError(f"No contractor_letter_settings row for {contractor_email!r} -- save settings before approving.")


def is_approval_current(settings: ContractorLetterSettings, current_fingerprint: str) -> bool:
    return bool(settings.approved and settings.approved_fingerprint == current_fingerprint)


def _privacy_contact_email() -> str:
    val = os.getenv(PRIVACY_CONTACT_EMAIL_ENV, "").strip()
    if not val:
        raise LetterConfigError(
            f"{PRIVACY_CONTACT_EMAIL_ENV} is not set. The letter template previously used two DIFFERENT "
            f"addresses in different places (contact@treekey.co.uk in main.py's letter, contact@treekey.uk "
            f"in privacy_policy_draft.md) -- refusing to guess which inbox is actually read. Set this "
            f"explicitly once confirmed."
        )
    return val


def _privacy_policy_url() -> str:
    return os.getenv(PRIVACY_POLICY_URL_ENV, "").strip() or "treekey.co.uk/privacy-policy"


def render_letter(settings: ContractorLetterSettings, *, lead_reference: str, address: str,
                   summary: str, council: str) -> str:
    """Pure function: same inputs always produce the same output (needed for
    fingerprinting to mean anything). No network, no DB. Raises
    LetterConfigError if required config is missing rather than silently
    substituting a guess -- see _privacy_contact_email above.

    2026-09-23 handoff: every dynamic value -- contractor-supplied AND
    lead-sourced (address/summary/council/lead_reference come from the
    leads table, not from this contractor, but are still external data) --
    is HTML-escaped here unconditionally, regardless of whether it already
    passed ContractorLetterSettings.validate()'s stricter '<'/'>' rejection.
    This is deliberate defense in depth: validate() is what a contractor
    saving settings through the real routes always goes through, but this
    function itself makes no assumption about how it was called (see
    ContractorLetterSettings.validate's own docstring for why both layers
    exist). The template shell (opening/quote-request/sign-off) is chosen
    by settings.template_key; the locked privacy/data-source footer is
    identical across all three templates -- see this module's docstring."""
    contact_email_footer = html.escape(_privacy_contact_email())
    policy_url = html.escape(_privacy_policy_url())

    template = TEMPLATE_REGISTRY.get(settings.template_key) or TEMPLATE_REGISTRY[DEFAULT_TEMPLATE_KEY]

    def esc(value) -> str:
        return html.escape((value or "").strip())

    business_name = esc(settings.business_name)
    phone = esc(settings.phone)
    contact_email = esc(settings.contact_email)
    service_area_note = esc(settings.service_area_note)
    business_intro = esc(settings.business_intro)
    services_note = esc(settings.services_note)
    lead_reference_e = esc(lead_reference)
    address_e = esc(address)
    summary_e = esc(summary)
    council_e = esc(council)

    opening_html = f'<p style="font-size:14px; text-align:justify;">{template.opening_line.format(council=council_e, lead_reference=lead_reference_e)}</p>'
    quote_request_html = f'<p style="font-size:14px; text-align:justify;">{esc(template.quote_request_line)}</p>'

    contact_email_line = f' &middot; {contact_email}' if contact_email else ""
    service_area_html = f'<div style="font-size:12px; color:#666;">Area covered: {service_area_note}</div>' if service_area_note else ""
    business_intro_html = f'<p style="font-size:14px;">{business_intro}</p>' if business_intro else ""
    services_html = f'<p style="font-size:14px;"><b>Services:</b> {services_note}</p>' if services_note else ""
    insurance_html = f'<p style="font-size:14px;">{esc(settings.insurance_note)}</p>' if settings.insurance_note.strip() else ""
    qualifications_html = f'<p style="font-size:14px;">{esc(settings.qualifications_note)}</p>' if settings.qualifications_note.strip() else ""

    return f"""<!DOCTYPE html>
<html lang="en-GB">
<head>
<meta charset="UTF-8">
<title>Homeowner Notice Letter | {lead_reference_e}</title>
<style>
body {{ font-family: "Georgia", serif; padding: 40px; color: #111; max-width: 650px; margin: auto; line-height: 1.6; background: #fff; }}
.header {{ border-bottom: 2px solid #044332; padding-bottom: 15px; margin-bottom: 25px; }}
.title {{ font-size: 20px; font-weight: bold; color: #044332; }}
</style>
</head>
<body>
<div class="header">
    <div class="title">{business_name}</div>
    <div style="font-size:12px; color:#666;">Tel: {phone}{contact_email_line}</div>
    {service_area_html}
</div>
<p style="font-size:14px;"><b>To the Property Owner / Occupier:</b><br>{address_e}</p>
<p style="font-size:14px;">Dear Homeowner,</p>
{opening_html}
{business_intro_html}
<div style="background:#f8fafc; border-left:3px solid #044332; padding:12px 16px; margin:15px 0; font-size:13px;">
    <b>Proposed Arboricultural Specification:</b><br><i>"{summary_e}"</i>
</div>
{services_html}
{insurance_html}
{qualifications_html}
{quote_request_html}
<div style="margin-top:30px; font-size:14px;">
{esc(template.sign_off_word)},<br><br><b>{business_name}</b><br>Direct Line: <b>{phone}</b>
</div>
<div style="margin-top:28px; padding-top:14px; border-top:1px solid #cbd5e1; font-size:10.5px; line-height:1.5; color:#64748b;">
<b>How we found your details.</b> This letter was prepared using information from your planning
application <b>{lead_reference_e}</b>, a public record held by {council_e}. TreeKey (operated by Vector Data
Labs) processed this information on the basis of its legitimate interest in connecting relevant local
contractors with published planning notices. You have the right to object to this processing, to ask what
information is held about you, or to request its removal from future matching &mdash; contact
<b>{contact_email_footer}</b>, or see the full privacy notice at <b>{policy_url}</b>, which includes our data
retention periods and your right to complain to the ICO. Objecting to this processing does not affect this
contractor's ability to assist with your project if you choose to contact them directly, and does not
retract the postal notice already provided to you.
</div>
</body>
</html>"""


def content_fingerprint(html_content: str) -> str:
    return hashlib.sha256(html_content.encode("utf-8")).hexdigest()
