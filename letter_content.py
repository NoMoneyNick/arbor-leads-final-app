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

import base64
import hashlib
import html
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

PRIVACY_CONTACT_EMAIL_ENV = "TREEKEY_PRIVACY_CONTACT_EMAIL"
PRIVACY_POLICY_URL_ENV = "TREEKEY_PRIVACY_POLICY_URL"

# 2026-09-24 handoff ("prepare the three-template system for the final
# TreeKey letters"): the reverse-page "Who is responsible?" notice can
# optionally include TreeKey's correspondence address. Nick's own
# 2026-09-22 handoff: "Nick's PO Box verification is under review; exact
# address, acceptance and permitted use are not confirmed here. Don't
# substitute the home address, invent a company number, invent a verified
# PO Box." UNLIKE PRIVACY_CONTACT_EMAIL_ENV below, this is deliberately
# OPTIONAL, not fail-loud: this address genuinely does not exist yet (it's
# not a config value someone forgot to set), and this is part of the
# minimum launch sequence -- a missing address must not block rendering
# every letter (preview included, which gates contractor onboarding) over
# an unresolved external business decision. Omitted from the notice
# entirely when unset (never invented, never a placeholder string) -- see
# _correspondence_address_clause below. Genuinely blocking: real posting
# already can't happen regardless (LETTER_SENDING_LIVE / FUNDING_MODE /
# no configured provider), so this doesn't weaken any safety gate, it only
# affects the wording of one reverse-page sentence.
CORRESPONDENCE_ADDRESS_ENV = "TREEKEY_CORRESPONDENCE_ADDRESS"


class LetterConfigError(RuntimeError):
    """Raised when required, non-guessable configuration is missing.
    Deliberately loud -- 'missing configuration must fail safely and
    visibly' (brief, Authorisation and boundaries)."""


# ---------------------------------------------------------------------------
# Brand -- 2026-09-24 handoff ("current TreeKey branding", "no invented
# claims"). Colours and asset choices below are NOT invented here: they are
# taken verbatim from Nick's own approved v2 design
# (handoff_inspect/build_letter_v2.py -- the exact hex values that script
# uses -- and handoff_inspect/README.txt's own split of which of the seven
# supplied brand assets are "modern" vs "tiny heritage placement only").
# ---------------------------------------------------------------------------
BRAND_GREEN = "#08795F"
BRAND_DARK = "#102D27"
BRAND_INK = "#243B35"
BRAND_MUTED = "#5B6C65"
BRAND_PALE = "#EDF6F1"
BRAND_TAGLINE_LINE_1 = "LOCAL CONNECTIONS."
BRAND_TAGLINE_LINE_2 = "PERSONAL INTRODUCTIONS."

# Asset files bundled with this code (not operator configuration -- see
# _brand_asset_data_uri below). treekey-full-logo.png and
# treekey-icon-badge-small.png are two of the four assets README.txt marks
# "modern branding"; treekey-tk-mark-small.png is one of the three marked
# "tiny heritage placement only" -- used here exactly that way (a small
# footer mark on both pages), matching build_letter_v2.py's own placement.
# The "-small" files are locally-resized copies of the originals (full
# print-resolution masters at 1200x1200px/560KB+ were needlessly large for
# a mark rendered at a few millimetres) -- same image, same design, smaller
# file. See app/assets/letter_branding/ for all three.
_ASSET_DIR = Path(__file__).resolve().parent / "assets" / "letter_branding"
BRAND_ASSET_FULL_LOGO = "treekey-full-logo.png"
BRAND_ASSET_ICON_BADGE_SMALL = "treekey-icon-badge-small.png"
BRAND_ASSET_TK_MARK_SMALL = "treekey-tk-mark-small.png"

_brand_asset_cache: "dict[str, str]" = {}


def _brand_asset_data_uri(filename: str) -> str:
    """Self-contained (no network, no /static mount dependency) so a
    rendered letter -- including the frozen approved_content_html snapshot
    stored per obligation -- carries its own branding regardless of
    whether a webserver is even running. Cached after first read (the
    files never change at runtime). A missing file is a PACKAGING problem
    (the asset should ship with the code in every checkpoint ZIP), not an
    operator configuration gap -- raises plainly rather than silently
    rendering a letter with no logo."""
    if filename not in _brand_asset_cache:
        path = _ASSET_DIR / filename
        try:
            raw = path.read_bytes()
        except OSError as e:
            raise RuntimeError(
                f"Letter branding asset {filename!r} is missing from {path}. This ships as part of "
                f"the code (app/assets/letter_branding/) -- if you see this, the asset was not "
                f"included when this checkpoint was deployed/extracted."
            ) from e
        _brand_asset_cache[filename] = f"data:image/png;base64,{base64.b64encode(raw).decode('ascii')}"
    return _brand_asset_cache[filename]


def _correspondence_address_clause() -> str:
    """Returns ', at <address>' if TREEKEY_CORRESPONDENCE_ADDRESS is set,
    or '' (grammatically clean either way) if not -- omit rather than
    invent, same principle already applied throughout this codebase to
    other fields that cannot be safely produced (see database.py's
    marketplace-summary redaction and address_release.py's guarded
    disclosures). Deliberately NOT fail-loud -- see CORRESPONDENCE_ADDRESS_ENV's
    own comment above for why a missing address must not block rendering."""
    val = os.getenv(CORRESPONDENCE_ADDRESS_ENV, "").strip()
    return f", at {html.escape(val)}" if val else ""


def _letter_date_today() -> str:
    """Europe/London calendar date, formatted for a printed letter (e.g.
    '24 September 2026'). Same zoneinfo("Europe/London") convention already
    used elsewhere in this codebase (see main.py/database.py). Deliberately
    computed HERE, inside render_letter, rather than threaded through as a
    parameter: render_letter's own 'pure function of its inputs' contract
    is about FINGERPRINTING (template_fingerprint never calls render_letter
    at all -- see that function's docstring), and the one place render_letter's
    real output is ever persisted (worker.promote_pending_approvals) captures
    the HTML immediately into approved_content_html at the moment of
    promotion and never re-renders afterwards -- so 'the date on the letter
    is the date it was actually frozen for sending' is exactly the correct
    behaviour, not a purity violation in practice."""
    import datetime
    from zoneinfo import ZoneInfo
    return datetime.datetime.now(ZoneInfo("Europe/London")).strftime("%-d %B %Y")


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

# 2026-09-24 handoff: bump this whenever the REVERSE PAGE's own wording
# (the "About this letter and your information" notice -- see render_letter)
# changes. The reverse page is TreeKey's own copy, identical across every
# contractor and every template (see
# tests/test_letter_content.py::test_locked_reverse_page_is_identical_across_every_template),
# exactly like TEMPLATE_REGISTRY's front-page shell text -- and the task's
# own requirement ("any wording/version change invalidates applicable
# reusable approval") applies to it the same way: see its inclusion in
# template_fingerprint's material, below, which is what actually makes
# editing this text invalidate every contractor's existing approval, not
# just this comment.
REVERSE_PAGE_CONTENT_VERSION = 1

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

    2026-09-24 handoff: also bakes in REVERSE_PAGE_CONTENT_VERSION. The
    reverse page (privacy/supporting information) added this pass is
    TreeKey's own locked copy, identical for every contractor and template
    -- not part of any one contractor's editable settings, and not part of
    TEMPLATE_REGISTRY either. Without including it here, a future edit to
    the reverse page's wording (e.g. once real legal review revises the
    retention text) would silently NOT invalidate any contractor's existing
    approval, even though that wording is baked into every frozen
    approved_content_html snapshot going forward -- the same gap the
    template-wording fingerprinting above already closes for front-page
    copy. Bump REVERSE_PAGE_CONTENT_VERSION whenever that wording changes.

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
        str(REVERSE_PAGE_CONTENT_VERSION),
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
    """Pure function of its settings/lead inputs for fingerprinting purposes
    (template_fingerprint never calls this -- see that function's own
    docstring); internally reads today's Europe/London date and the two
    module-level config values below, neither of which affects what
    template_fingerprint tracks. No network, no DB. Raises LetterConfigError
    if required config is missing rather than silently substituting a
    guess -- see _privacy_contact_email above.

    2026-09-24 handoff ("prepare the three-template system for the final
    TreeKey letters"): produces a genuine TWO-PAGE document -- a front page
    (the contractor introduction) and a reverse page (supporting/privacy
    information only, no contractor content or advertisement) -- matching
    the two-sided A4 layout Nick approved in
    handoff_inspect/TreeKey-branded-letter-draft-v2.pdf. Each page is a
    fixed A4-sized block with 'page-break-after: always' between them in
    print/PDF output, so printing or exporting this document produces
    exactly two pages, not one long scroll. The PER-TEMPLATE wording
    (opening/quote-request/sign-off) is UNCHANGED from before this pass --
    still Nick's placeholder copy pending his final wording (see this
    module's docstring) -- only REPOSITIONED into the new two-page layout.
    The reverse page's content is new this pass and is built entirely from
    facts already established and verified elsewhere in this codebase (the
    lawful basis already published on /privacy-policy, the address-hiding
    behaviour address_release.py actually implements, the 72-hour/60-day
    retention periods actually implemented and tested this session) -- not
    invented, and not copied from the "DESIGN DRAFT -- NOT FOR POSTING"
    v2 PDF's own bracketed placeholder text.

    Every dynamic value -- contractor-supplied AND lead-sourced
    (address/summary/council/lead_reference come from the leads table, not
    from this contractor, but are still external data) -- is HTML-escaped
    here unconditionally, regardless of whether it already passed
    ContractorLetterSettings.validate()'s stricter '<'/'>' rejection. This
    is deliberate defense in depth: validate() is what a contractor saving
    settings through the real routes always goes through, but this
    function itself makes no assumption about how it was called."""
    contact_email_footer = html.escape(_privacy_contact_email())
    policy_url = html.escape(_privacy_policy_url())
    letter_date = _letter_date_today()
    correspondence_clause = _correspondence_address_clause()

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

    opening_html = f'<p class="body-text">{template.opening_line.format(council=council_e, lead_reference=lead_reference_e)}</p>'
    quote_request_html = f'<p class="body-text">{esc(template.quote_request_line)}</p>'

    contact_email_line = f' &middot; {contact_email}' if contact_email else ""
    service_area_html = f'<div class="meta-line">Area covered: {service_area_note}</div>' if service_area_note else ""
    business_intro_html = (
        f'<p class="body-text"><b>About the business.</b> {business_intro}</p>' if business_intro else ""
    )
    services_html = f'<p class="body-text"><b>Services:</b> {services_note}</p>' if services_note else ""
    insurance_html = f'<p class="body-text">{esc(settings.insurance_note)}</p>' if settings.insurance_note.strip() else ""
    qualifications_html = f'<p class="body-text">{esc(settings.qualifications_note)}</p>' if settings.qualifications_note.strip() else ""

    full_logo_uri = _brand_asset_data_uri(BRAND_ASSET_FULL_LOGO)
    icon_badge_uri = _brand_asset_data_uri(BRAND_ASSET_ICON_BADGE_SMALL)
    tk_mark_uri = _brand_asset_data_uri(BRAND_ASSET_TK_MARK_SMALL)

    front_page = f"""<div class="letter-page">
  <div class="brand-header">
    <img class="brand-header-logo" src="{full_logo_uri}" alt="TreeKey">
    <div class="brand-header-tagline">
      <div>{BRAND_TAGLINE_LINE_1}</div>
      <div class="brand-header-tagline-sub">{BRAND_TAGLINE_LINE_2}</div>
    </div>
  </div>

  <div class="front-top-row">
    <div class="recipient-block">
      <div class="meta-label">To the Property Owner / Occupier</div>
      <div class="recipient-address">{address_e}</div>
    </div>
    <div class="intro-ref-block">
      <div class="intro-ref-label">Your Introduction</div>
      <div class="meta-line">Reference: {lead_reference_e}</div>
      <div class="meta-line">{letter_date}</div>
    </div>
  </div>

  <p class="body-text">Dear homeowner,</p>
  {opening_html}
  {business_intro_html}

  <div class="spec-box">
    <b>Proposed Arboricultural Specification:</b><br><i>&ldquo;{summary_e}&rdquo;</i>
  </div>
  {services_html}
  {insurance_html}
  {qualifications_html}
  {quote_request_html}

  <div class="contact-panel">
    <div class="contact-panel-label">Speak Directly To Your Tree Surgeon</div>
    <div class="contact-panel-phone">{phone}</div>
    <div class="meta-line">{business_name}{contact_email_line}</div>
  </div>
  {service_area_html}

  <p class="disclaimer-text">
    {esc(template.sign_off_word)}, {business_name}
  </p>
  <p class="disclaimer-text">
    There is no obligation. If you have already appointed someone, you can simply disregard this letter.
    TreeKey arranged this introduction; {business_name} would provide any quotation and carry out any
    agreed work.
  </p>
  <p class="disclaimer-text">
    Why this reached you: we used information from a public council planning register. See the reverse
    for supporting and privacy information.
  </p>

  {_brand_footer_html(tk_mark_uri, page_number=1)}
</div>"""

    reverse_page = f"""<div class="letter-page">
  <div class="reverse-header">
    <img class="reverse-header-mark" src="{icon_badge_uri}" alt="TreeKey">
    <div class="reverse-header-word">TREEKEY</div>
  </div>
  <div class="reverse-title">About this letter and your information</div>

  <div class="notice-section">
    <div class="notice-heading">Who is responsible?</div>
    <p class="notice-body">TreeKey is operated by Vector Data Labs, trading as TreeKey{correspondence_clause}.
    Contact: <b>{contact_email_footer}</b>.</p>
  </div>

  <div class="notice-section">
    <div class="notice-heading">Where did the information come from?</div>
    <p class="notice-body">We used information from your planning application <b>{lead_reference_e}</b>,
    a public record held by {council_e}.</p>
  </div>

  <div class="notice-section">
    <div class="notice-heading">Why is it used?</div>
    <p class="notice-body">TreeKey uses relevant planning information to identify possible tree-work
    opportunities and arrange introductions from local tree-work contractors, on the basis of its
    legitimate interests under UK GDPR Article 6(1)(f) in connecting relevant local contractors with
    published planning notices.</p>
  </div>

  <div class="notice-section">
    <div class="notice-heading">Who receives it?</div>
    <p class="notice-body">We have not given {business_name} your name or postal address. They receive
    only a general description of the work and the wider area. Our printing and postal provider need
    your address to produce and deliver this letter.</p>
  </div>

  <div class="notice-section">
    <div class="notice-heading">How long is it kept?</div>
    <p class="notice-body">Once this letter has been dispatched, we delete your name, address and this
    letter's content from our live systems within 72 hours. A minimal record of the transaction (needed
    for accounting and to handle any complaint) is kept for longer; we have not yet set a fixed expiry
    for that minimal record.</p>
  </div>

  <div class="notice-section">
    <div class="notice-heading">Stop further marketing</div>
    <p class="notice-body">You can object to this or any future TreeKey marketing at any time. Email
    <b>{contact_email_footer}</b> with your address and reference <b>{lead_reference_e}</b>; no
    explanation is required. TreeKey will not send another marketing letter about this application.
    Objecting does not retract this letter or affect this contractor's ability to assist with your
    project if you choose to contact them directly.</p>
  </div>

  <div class="notice-section">
    <div class="notice-heading">Your other rights</div>
    <p class="notice-body">You can also ask to see, correct or delete the information we hold, or ask us
    to restrict how we use it, where applicable. You can complain to the Information Commissioner's
    Office at ico.org.uk.</p>
  </div>

  <div class="notice-section">
    <div class="notice-heading">Full privacy information</div>
    <p class="notice-body">Our full privacy notice, including our data retention periods, is at
    <b>{policy_url}</b>.</p>
  </div>

  {_brand_footer_html(tk_mark_uri, page_number=2)}
</div>"""

    return f"""<!DOCTYPE html>
<html lang="en-GB">
<head>
<meta charset="UTF-8">
<title>Homeowner Notice Letter | {lead_reference_e}</title>
<style>
{_LETTER_PAGE_CSS}
</style>
</head>
<body>
{front_page}
{reverse_page}
</body>
</html>"""


def _brand_footer_html(tk_mark_uri: str, *, page_number: int) -> str:
    """Shared tiny footer mark, identical shape on both pages (a real
    physical letter's page numbering, not contractor content) -- reuses
    the heritage tk-mark asset at the same tiny size the approved v2
    design used it at (README.txt: 'for tiny heritage placement only')."""
    return f"""<div class="page-footer">
    <img class="page-footer-mark" src="{tk_mark_uri}" alt="">
    <span class="page-footer-domain">treekey.co.uk</span>
    <span class="page-footer-pageno">{page_number} / 2</span>
  </div>"""


# Print-safe, fixed-size A4 page CSS shared by both pages. Deliberately NO
# dynamic font-size scaling anywhere -- "do not shrink text automatically
# to force overflowing content to fit" (2026-09-24 handoff). Overflowing
# content is instead rejected up front by ContractorLetterSettings.validate's
# length limits (see MAX_* below) -- a fixed, predictable layout that
# either fits or is refused at save time, never a layout that silently
# compresses to fit whatever was typed. word-break/overflow-wrap on every
# text element handle a single very long word (e.g. a long business name)
# without clipping or overflowing its box.
_LETTER_PAGE_CSS = f"""
* {{ box-sizing: border-box; }}
body {{ margin: 0; padding: 0; background: #d9d9d9; font-family: Georgia, "Times New Roman", serif; }}
.letter-page {{
  width: 210mm; min-height: 297mm; margin: 0 auto; background: #fff; color: {BRAND_INK};
  padding: 16mm 18mm 14mm 18mm; position: relative; line-height: 1.5;
  word-break: break-word; overflow-wrap: break-word;
}}
@media print {{
  body {{ background: #fff; }}
  .letter-page {{ margin: 0; page-break-after: always; }}
  .letter-page:last-child {{ page-break-after: auto; }}
}}
@media screen {{
  .letter-page {{ margin-bottom: 8mm; box-shadow: 0 0 6px rgba(0,0,0,0.15); }}
}}
.body-text {{ font-size: 12.5px; text-align: justify; margin: 0 0 10px 0; }}
.meta-line {{ font-size: 11px; color: {BRAND_MUTED}; }}
.meta-label {{ font-size: 10px; letter-spacing: 0.04em; text-transform: uppercase; color: {BRAND_MUTED}; margin-bottom: 3px; }}
.disclaimer-text {{ font-size: 10px; line-height: 1.5; color: {BRAND_MUTED}; margin: 8px 0; }}

.brand-header {{
  background: {BRAND_DARK}; border-radius: 6px; padding: 10px 18px; margin-bottom: 14mm;
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
}}
.brand-header-logo {{ height: 13mm; max-width: 60%; object-fit: contain; }}
.brand-header-tagline {{ color: #fff; font-size: 8px; font-weight: bold; letter-spacing: 0.03em; text-align: right; }}
.brand-header-tagline-sub {{ color: #A5E5CD; font-weight: normal; margin-top: 2px; }}

.front-top-row {{ display: flex; justify-content: space-between; gap: 16px; margin-bottom: 10mm; }}
.recipient-block {{ max-width: 60%; }}
.recipient-address {{ font-size: 12.5px; font-weight: bold; white-space: pre-line; }}
.intro-ref-block {{ text-align: right; }}
.intro-ref-label {{ font-size: 10.5px; font-weight: bold; color: {BRAND_GREEN}; margin-bottom: 4px; }}

.spec-box {{ background: {BRAND_PALE}; border-left: 3px solid {BRAND_GREEN}; padding: 10px 14px; margin: 12px 0; font-size: 12px; }}

.contact-panel {{ background: {BRAND_PALE}; border-left: 3px solid {BRAND_GREEN}; border-radius: 4px; padding: 12px 16px; margin: 14px 0; }}
.contact-panel-label {{ font-size: 10.5px; font-weight: bold; color: {BRAND_GREEN}; letter-spacing: 0.02em; text-transform: uppercase; margin-bottom: 6px; }}
.contact-panel-phone {{ font-size: 17px; font-weight: bold; color: {BRAND_DARK}; margin-bottom: 4px; }}

.reverse-header {{ display: flex; align-items: center; gap: 8px; margin-bottom: 4mm; }}
.reverse-header-mark {{ height: 7mm; width: 7mm; object-fit: contain; }}
.reverse-header-word {{ font-size: 12px; font-weight: bold; letter-spacing: 0.04em; color: {BRAND_DARK}; }}
.reverse-title {{ font-size: 14px; font-weight: bold; margin-bottom: 8mm; color: {BRAND_INK}; }}

.notice-section {{ margin-bottom: 9px; }}
.notice-heading {{ font-size: 10.5px; font-weight: bold; color: {BRAND_INK}; margin-bottom: 2px; }}
.notice-body {{ font-size: 9.5px; line-height: 1.45; color: #333D38; margin: 0; }}

.page-footer {{
  position: absolute; left: 18mm; right: 18mm; bottom: 8mm;
  border-top: 1px solid #D7E5DD; padding-top: 6px;
  display: flex; align-items: center; gap: 8px; font-size: 9px; color: {BRAND_MUTED};
}}
.page-footer-mark {{ height: 5mm; width: auto; }}
.page-footer-domain {{ color: {BRAND_GREEN}; font-weight: bold; }}
.page-footer-pageno {{ margin-left: auto; }}
"""


def content_fingerprint(html_content: str) -> str:
    return hashlib.sha256(html_content.encode("utf-8")).hexdigest()
