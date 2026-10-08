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


def _correspondence_address_text() -> str:
    """The escaped correspondence address from TREEKEY_CORRESPONDENCE_ADDRESS,
    or '' when it is not set (never invented, never a placeholder). Used by the
    reverse-page "Who is responsible?" and "Stop further marketing" notices,
    which offer a postal route only when an address is actually configured."""
    return html.escape(os.getenv(CORRESPONDENCE_ADDRESS_ENV, "").strip())


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
    behaviour, not a purity violation in practice.

    2026-09-26 fix: previously used strftime("%-d %B %Y") for an unpadded
    day number. "%-d" is a glibc/Linux strftime extension, not part of the
    C standard -- it raises ValueError: Invalid format string on Windows
    (Python there uses the platform C runtime's strftime, which does not
    recognise "%-d"; Windows' own unpadded-day directive is "%#d" instead,
    which is in turn not portable to Linux). Nick hit this running
    scripts/intelliprint_test_send.py locally on Windows. Fixed
    platform-independently by taking the day number directly from the
    date object (an int, so it's naturally unpadded, e.g. 4 not 04) and
    using strftime only for the month/year, which has no padding-style
    platform difference."""
    import datetime
    from zoneinfo import ZoneInfo
    today = datetime.datetime.now(ZoneInfo("Europe/London"))
    return f"{today.day} {today.strftime('%B %Y')}"


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

# 2026-09-27 fix ("restore the visual design"): Nick supplied
# TreeKey-short-review.pdf as the visual source of truth for typography,
# header prominence, a front-page headline, the contact box, and
# front/reverse hierarchy -- all of which this file already had except the
# headline, which had been dropped somewhere between that design and this
# file's current front_page markup. Restored as a single shared line (like
# REVERSE_PAGE_CONTENT_VERSION above), not a per-template field, because
# the reference shows one generic headline, not three tonal variants, and
# because TEMPLATE_REGISTRY's three shells are wired for THEIR OWN
# versioning already -- adding a 4th shell field to all three for one
# shared line would be more machinery than the change needs.
#   WORDING STATUS: copied verbatim from the reference PDF, which is NOT
# bracketed like its [Recipient name]/[Business name] placeholders --
# but per this file's own "PLACEHOLDER COPY, DELIBERATELY" section above,
# EVERY front-page string in this file (TEMPLATE_REGISTRY included) is
# still Nick's placeholder pending his final copy, not approved live
# wording. Treat this headline the same way: draft, not confirmed, same as
# the rest of the front page around it, until Nick says otherwise.
# Bump this whenever the headline text itself changes -- exactly the same
# reasoning as REVERSE_PAGE_CONTENT_VERSION, and it is hashed into
# template_fingerprint alongside it below for the same reason: so a future
# copy edit invalidates existing reusable approvals instead of silently
# rendering new text under an old approval.
FRONT_PAGE_HEADLINE_VERSION = 1
FRONT_PAGE_HEADLINE = "Looking for a quote for your tree work?"

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


# 2026-09-30: telephone validation and small identity/formatting helpers.
# No phone-validation utility existed in the codebase, and the `phonenumbers`
# package is not a dependency (adding one is a deployment change), so this is
# a deliberately tolerant structural check, not a country-specific one:
# ITU E.164 allows at most 15 digits including the country code, and no real
# number has fewer than about 7, so the accepted range is 7-15 digits. It does
# NOT force a UK format or a fixed length -- "01234 567890", "+44 1234 567890",
# "0044 1234 567890", "+1 (415) 555-2671" and "+353 87 123 4567" all pass.
_PHONE_ALLOWED_CHARS = re.compile(r"^\+?[0-9 ()./-]+$")
PHONE_FORMAT_HINT = ("Enter a valid telephone number, for example 01234 567890 or +44 1234 567890. "
                     "Spaces, brackets, dots, hyphens and a leading + are fine.")


def validate_phone(raw: str):
    """Returns (ok, message). Structural check only -- it cannot know whether
    a number is connected, and does not claim to."""
    value = (raw or "").strip()
    if not value:
        return False, "Please enter a telephone number."
    if len(value) > MAX_PHONE_LEN:
        return False, f"Your telephone number must be {MAX_PHONE_LEN} characters or fewer."
    if not _PHONE_ALLOWED_CHARS.match(value):
        return False, PHONE_FORMAT_HINT
    digits = re.sub(r"\D", "", value)
    if len(digits) < 7 or len(digits) > 15:
        return False, PHONE_FORMAT_HINT
    if len(set(digits)) == 1:
        return False, PHONE_FORMAT_HINT
    return True, ""


def phone_identity_key(raw: str) -> str:
    """Stable key for "is this the same telephone number": digits only, with
    the international prefix or national trunk zero ignored by keeping the
    last 10 digits, so "01234 567890" and "+44 1234 567890" match. Used only
    for one-per-business abuse control, never shown or printed."""
    digits = re.sub(r"\D", "", raw or "")
    return digits[-10:] if len(digits) >= 10 else digits


_BUSINESS_SUFFIXES = {"ltd", "limited", "llp", "plc", "uk", "the", "and", "co", "company", "services", "service"}


def business_identity_key(name: str) -> str:
    """Stable key for "is this the same business name": lower-case letters and
    digits only, ignoring generic company-form words, so "Ashcroft Tree
    Surgery Ltd" and "ashcroft tree surgery" match. Used only for
    one-per-business abuse control."""
    words = re.findall(r"[a-z0-9]+", (name or "").lower())
    words = [w for w in words if w not in _BUSINESS_SUFFIXES]
    return "".join(words)


_PLACE_LOWER_WORDS = {"and", "of", "the", "on", "upon", "in", "le", "de", "near", "around", "within", "miles",
                      "mile", "radius", "covering", "across", "from", "to", "by", "with", "for", "a", "an",
                      "surrounding", "area", "areas", "km", "approx", "about", "nearby", "under", "over", "at"}


def suggest_place_capitalisation(text: str):
    """Non-blocking suggestion for the Service area field. Returns a suggested
    string, or None when nothing should be suggested. Only words that are
    ENTIRELY lower-case are ever changed (so acronyms, postcodes such as NG22,
    "McDonald", "AONB" and anything the person capitalised deliberately are
    left alone), joining words such as "and"/"of"/"on" stay lower-case, and
    nothing is applied automatically. Not used for business names or letter
    wording."""
    value = (text or "").strip()
    if not value:
        return None

    def fix_word(word: str) -> str:
        if not word.isalpha() or not word.islower():
            return word
        if word in _PLACE_LOWER_WORDS:
            return word
        return word[:1].upper() + word[1:]

    def fix_token(token: str) -> str:
        trail = re.search(r"[.,;:]+$", token)
        if trail:
            return fix_token(token[:trail.start()]) + trail.group(0)
        parts = re.split(r"([-'\u2019])", token)
        out = []
        for i, part in enumerate(parts):
            if part in ("-", "'", "\u2019"):
                out.append(part)
            elif i > 0 and parts[i - 1] in ("'", "\u2019"):
                out.append(part)  # "king's" -> "King's", never "King'S"
            else:
                out.append(fix_word(part))
        return "".join(out)

    suggestion = " ".join(fix_token(t) for t in value.split(" "))
    # The very first word of a sentence-style entry ("covering leeds") is a
    # lower-case joining word: leave it, the place names after it are what matter.
    return suggestion if suggestion != value else None
MAX_CONTACT_EMAIL_LEN = 254
# 2026-09-27, contact panel redesign: a first name only, never a full name
# or surname -- kept short deliberately (see ContractorLetterSettings.
# contact_first_name's own comment for why this exists at all).
MAX_CONTACT_FIRST_NAME_LEN = 40
MAX_SERVICE_AREA_LEN = 160
MAX_BUSINESS_INTRO_LEN = 500
MAX_SERVICES_NOTE_LEN = 300
MAX_INSURANCE_LEN = 300
MAX_QUALIFICATIONS_LEN = 300
# 2026-09-30 handoff ("optional contractor offer, for launch"): the
# contractor's own wording, supplied and approved by them -- never invented
# or prefilled by TreeKey (see ContractorLetterSettings.offer_text's own
# comment). Kept short deliberately: this is a compact line/two beside the
# contact panel, not a second body of copy.
MAX_OFFER_TEXT_LEN = 200
MAX_OFFER_CODE_LEN = 30
MAX_OFFER_CONDITIONS_LEN = 200
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
        -- 2026-09-27, contact panel redesign: an optional first name shown
        -- alongside the business name and phone in the letter's contact
        -- panel -- "use saved details only; omit a missing first name"
        -- (Nick's own wording), so this is genuinely optional and blank by
        -- default, same idempotent-add-column pattern as contact_email above.
        ALTER TABLE contractor_letter_settings ADD COLUMN IF NOT EXISTS contact_first_name TEXT;
        -- 2026-09-30 handoff ("optional contractor offer, for launch"): an
        -- optional offer the contractor supplies and approves themselves --
        -- blank by default, same idempotent-add-column pattern as
        -- contact_first_name above. Three separate fields (wording, an
        -- optional code, and optional conditions/expiry) rather than one
        -- freeform blob, matching how the request itself separates them and
        -- letting the code be shown/styled distinctly from the wording.
        ALTER TABLE contractor_letter_settings ADD COLUMN IF NOT EXISTS offer_text TEXT;
        ALTER TABLE contractor_letter_settings ADD COLUMN IF NOT EXISTS offer_code TEXT;
        ALTER TABLE contractor_letter_settings ADD COLUMN IF NOT EXISTS offer_conditions TEXT;
        -- 2026-09-30, integrated first-time signup: the responsible
        -- contact's full name and the moment the Terms checkbox was ticked
        -- on the signup form. Account-identity records, NOT letter content:
        -- deliberately outside ContractorLetterSettings, the approval
        -- fingerprint and every letter render, so they can never appear on
        -- (or force re-approval of) a letter. NULL for accounts that
        -- existed before this signup form.
        ALTER TABLE contractor_letter_settings ADD COLUMN IF NOT EXISTS responsible_contact_name TEXT;
        ALTER TABLE contractor_letter_settings ADD COLUMN IF NOT EXISTS terms_accepted_at TIMESTAMPTZ;
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
    # 2026-09-27, contact panel redesign ("prominently show the contractor's
    # company name, contact's first name if supplied, and telephone number...
    # use saved details only; omit a missing first name" -- Nick's own
    # wording): a first name only, genuinely optional, shown on the letter
    # only when actually saved -- never invented or guessed from anywhere
    # else (e.g. the account's own login email/company name).
    contact_first_name: str = ""
    # 2026-09-30 handoff ("optional contractor offer, for launch"): supplied
    # and approved by the contractor themselves, exactly like every other
    # editable field above -- TreeKey never invents or prefills a discount
    # or "exclusive price" claim for a real contractor (see render_letter's
    # offer_html for the "blank means nothing appears" behaviour this
    # enforces). offer_code identifies a shared promotion (e.g. for the
    # contractor's own tracking); it is NOT the per-introduction identifier
    # -- that's letter_number, a completely separate mechanism (see
    # render_letter's letter_number parameter).
    offer_text: str = ""
    offer_code: str = ""
    offer_conditions: str = ""

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
        else:
            _phone_ok, _phone_msg = validate_phone(self.phone)
            if not _phone_ok and len(self.phone) <= MAX_PHONE_LEN:
                problems.append(f"phone: {_phone_msg}")
        if self.template_key not in TEMPLATE_REGISTRY:
            problems.append(
                f"template_key must be one of {sorted(TEMPLATE_REGISTRY.keys())}, got {self.template_key!r}"
            )

        length_limited_fields = (
            ("business_name", self.business_name, MAX_BUSINESS_NAME_LEN),
            ("phone", self.phone, MAX_PHONE_LEN),
            ("contact_email", self.contact_email, MAX_CONTACT_EMAIL_LEN),
            ("contact_first_name", self.contact_first_name, MAX_CONTACT_FIRST_NAME_LEN),
            ("offer_text", self.offer_text, MAX_OFFER_TEXT_LEN),
            ("offer_code", self.offer_code, MAX_OFFER_CODE_LEN),
            ("offer_conditions", self.offer_conditions, MAX_OFFER_CONDITIONS_LEN),
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
               template_key, business_intro, services_note, contact_email, contact_first_name,
               offer_text, offer_code, offer_conditions
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
        services_note=row[11] or "", contact_email=row[12] or "", contact_first_name=row[13] or "",
        offer_text=row[14] or "", offer_code=row[15] or "", offer_conditions=row[16] or "",
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
             template_key, business_intro, services_note, contact_email, contact_first_name,
             offer_text, offer_code, offer_conditions, template_version,
             approved, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, FALSE, NOW())
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
            contact_first_name = EXCLUDED.contact_first_name,
            offer_text = EXCLUDED.offer_text,
            offer_code = EXCLUDED.offer_code,
            offer_conditions = EXCLUDED.offer_conditions,
            template_version = contractor_letter_settings.template_version + 1,
            approved = FALSE,
            approved_fingerprint = NULL,
            approved_at = NULL,
            updated_at = NOW();
    """, (settings.contractor_email.strip().lower(), settings.business_name.strip(), settings.phone.strip(),
          settings.service_area_note.strip(), settings.insurance_note.strip(), settings.qualifications_note.strip(),
          settings.template_key.strip(), settings.business_intro.strip(), settings.services_note.strip(),
          settings.contact_email.strip(), settings.contact_first_name.strip(),
          settings.offer_text.strip(), settings.offer_code.strip(), settings.offer_conditions.strip(),
          settings.template_version))


def insert_initial_contractor_settings(cur, settings: ContractorLetterSettings,
                                       responsible_contact_name: str = "",
                                       terms_accepted_at: Optional[str] = None) -> bool:
    """First-time signup only: INSERT ... ON CONFLICT DO NOTHING. Unlike
    upsert_contractor_settings this can never overwrite an existing row --
    it returns False (and changes nothing) if the account already has
    settings. Always creates the row unapproved; approval stays a separate,
    explicit step (approve_template). The responsible-contact name and terms
    timestamp are written in the same statement so they exist only together
    with the row they belong to."""
    problems = settings.validate()
    if problems:
        raise ValueError(f"Invalid contractor letter settings: {'; '.join(problems)}")
    cur.execute("""
        INSERT INTO contractor_letter_settings
            (contractor_email, business_name, phone, service_area_note, insurance_note, qualifications_note,
             template_key, business_intro, services_note, contact_email, contact_first_name,
             offer_text, offer_code, offer_conditions, template_version,
             approved, responsible_contact_name, terms_accepted_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, FALSE, %s, %s, NOW())
        ON CONFLICT (contractor_email) DO NOTHING;
    """, (settings.contractor_email.strip().lower(), settings.business_name.strip(), settings.phone.strip(),
          settings.service_area_note.strip(), settings.insurance_note.strip(), settings.qualifications_note.strip(),
          settings.template_key.strip(), settings.business_intro.strip(), settings.services_note.strip(),
          settings.contact_email.strip(), settings.contact_first_name.strip(),
          settings.offer_text.strip(), settings.offer_code.strip(), settings.offer_conditions.strip(),
          settings.template_version, (responsible_contact_name or "").strip() or None, terms_accepted_at))
    return cur.rowcount == 1


def get_account_identity(cur, contractor_email: str) -> Optional[dict]:
    """The account-identity fields captured at signup, kept apart from the
    letter content: {"responsible_contact_name", "terms_accepted_at"}, or
    None if the account has no settings row."""
    cur.execute("SELECT responsible_contact_name, terms_accepted_at FROM contractor_letter_settings "
                "WHERE contractor_email = %s;", (contractor_email.strip().lower(),))
    row = cur.fetchone()
    if not row:
        return None
    return {"responsible_contact_name": row[0], "terms_accepted_at": row[1]}


def update_responsible_contact_name(cur, contractor_email: str, name: str) -> bool:
    """For later account editing. Changes ONLY the responsible-contact name:
    it never touches the letter content, approval, or the Terms acceptance
    record, so editing it cannot force re-approval or rewrite consent.
    Returns False if the account has no settings row or the name is blank
    (the field is required, so it cannot be blanked out)."""
    name = (name or "").strip()
    if not name or len(name) > 80:
        return False
    cur.execute("UPDATE contractor_letter_settings SET responsible_contact_name = %s "
                "WHERE contractor_email = %s;", (name, contractor_email.strip().lower()))
    return cur.rowcount == 1


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
    # 2026-09-27, contact panel redesign: contact_first_name joins
    # contact_email in this list -- it's shown on the letter (in the contact
    # panel, when saved) exactly like every other editable field above, so
    # editing it must invalidate reusable approval the same way editing
    # business_name or phone always has.
    # 2026-09-30, optional contractor offer: offer_text/offer_code/
    # offer_conditions join the same list for the same reason -- "changing
    # the offer must require reapproval for future mailings" (the request's
    # own wording). Deliberately NOT including letter_number here: it is a
    # per-obligation value assigned once an introduction is actually
    # created, never part of the reusable TEMPLATE a contractor approves
    # (see render_letter's own letter_number parameter and comment).
    template = TEMPLATE_REGISTRY.get(settings.template_key) or TEMPLATE_REGISTRY[DEFAULT_TEMPLATE_KEY]
    material = "\x1f".join([
        settings.business_name.strip(), settings.phone.strip(), settings.contact_email.strip(),
        settings.contact_first_name.strip(),
        settings.offer_text.strip(), settings.offer_code.strip(), settings.offer_conditions.strip(),
        settings.template_key.strip(), str(template.version),
        template.opening_line, template.quote_request_line, template.sign_off_word,
        settings.business_intro.strip(), settings.services_note.strip(),
        settings.service_area_note.strip(), settings.insurance_note.strip(),
        settings.qualifications_note.strip(), str(settings.template_version),
        str(REVERSE_PAGE_CONTENT_VERSION), str(FRONT_PAGE_HEADLINE_VERSION),
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
# 2026-09-30 handoff ("simple letter-number matching"): a clearly-fictional
# sample number for the same reason PREVIEW_LEAD_REFERENCE etc above are
# fictional -- this preview/approval UI never shows a real, in-use letter
# number to a contractor.
PREVIEW_LETTER_NUMBER = 1042


def render_preview_letter(settings: ContractorLetterSettings) -> str:
    """The exact same render_letter function a real send uses, against the
    fixed sample data above -- so what a contractor approves here is
    provably rendered by the same code path as a real letter, not a
    second, potentially-diverging preview template. Also, therefore, the
    same code path that fulfils "generate a preview ... through the same
    rendering path used for fulfilment" (2026-09-23 handoff) -- there is
    no separate preview renderer to keep in sync."""
    return render_letter(settings, lead_reference=PREVIEW_LEAD_REFERENCE, address=PREVIEW_ADDRESS,
                          summary=PREVIEW_SUMMARY, council=PREVIEW_COUNCIL,
                          letter_number=PREVIEW_LETTER_NUMBER)


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


def format_address_lines(address: str) -> list:
    """Display-only: the lines the recipient address block prints, one per line.

    2 Oct 2026 layout fix: the address used to print as ONE wrapped run of text
    ("124 Coulsdon Road, Old / Coulsdon, CR5 2LE"), breaking mid-name. Royal
    Mail's layout is one part per line with the postcode last, and Intelliprint
    reads the address off the page.

    Strictly line breaks, never content:
      - If the stored address already contains line breaks, those lines are used
        exactly as stored (commas inside them are NOT split).
      - Otherwise it is split ONLY at the commas that are already there.
      - Nothing is added, removed, reordered, re-cased or "corrected": no postcode
        is split off a line that has no comma, and no post town is guessed.
      - Only leading/trailing blanks around each part and empty parts are dropped.
    The stored address (database, frozen letter content, provider request) is not
    changed by this; only how the page prints it.
    """
    text = (address or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if "\n" in text:
        parts = text.split("\n")
    else:
        parts = text.split(",")
    lines = [p.strip() for p in parts if p.strip()]
    return lines or ([text] if text else [])


def render_letter(settings: ContractorLetterSettings, *, lead_reference: str, address: str,
                   summary: str, council: str, letter_number: Optional[int] = None) -> str:
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
    function itself makes no assumption about how it was called.

    2026-09-30 handoff ("simple letter-number matching"): letter_number is
    the short, sequential, per-introduction reference ("Letter number:
    1042") -- deliberately a plain optional parameter, not something this
    function derives itself, so it stays a pure function of its inputs
    (matching this docstring's opening sentence) and callers remain fully
    responsible for sourcing it correctly (from letter_obligations.
    letter_number -- see worker.promote_pending_approvals and main.py's
    generate_homeowner_letter route -- never from lead_reference/address/
    council). None is accepted (renders no line) only so existing callers
    that genuinely have no obligation row yet cannot break; both real
    production call sites always pass a real value."""
    contact_email_footer = html.escape(_privacy_contact_email())
    policy_url = html.escape(_privacy_policy_url())
    letter_date = _letter_date_today()
    correspondence_text = _correspondence_address_text()
    # Release wording (7 Oct 2026, approved): the postal route is offered only when an address
    # is configured; the e-mail route is always offered.
    write_or_email = (f"You can write to {correspondence_text} or email <b>{contact_email_footer}</b>."
                      if correspondence_text else f"You can email <b>{contact_email_footer}</b>.")
    objection_route = (f"Email <b>{contact_email_footer}</b> or write to the address above"
                       if correspondence_text else f"Email <b>{contact_email_footer}</b>")

    template = TEMPLATE_REGISTRY.get(settings.template_key) or TEMPLATE_REGISTRY[DEFAULT_TEMPLATE_KEY]

    def esc(value) -> str:
        return html.escape((value or "").strip())

    business_name = esc(settings.business_name)
    phone = esc(settings.phone)
    contact_email = esc(settings.contact_email)
    contact_first_name = esc(settings.contact_first_name)
    service_area_note = esc(settings.service_area_note)
    business_intro = esc(settings.business_intro)
    services_note = esc(settings.services_note)
    lead_reference_e = esc(lead_reference)
    address_e = "\n".join(html.escape(line) for line in format_address_lines(address))
    summary_e = esc(summary)
    council_e = esc(council)

    opening_html = f'<p class="body-text">{template.opening_line.format(council=council_e, lead_reference=lead_reference_e)}</p>'
    quote_request_html = f'<p class="body-text">{esc(template.quote_request_line)}</p>'

    # 2026-09-27, contact panel redesign ("prominently show the contractor's
    # company name, contact's first name if supplied, and telephone number
    # in the contact panel. Use saved details only; omit a missing first
    # name" -- Nick's own wording, this exact pass): business name and phone
    # are always available (both required by validate()), so they always
    # render; contact_first_name is genuinely optional and simply omitted --
    # never a placeholder or invented name -- when nothing is saved.
    # contact_email keeps its own separate, smaller line beneath, unchanged
    # from before this pass.
    contact_first_name_html = (
        f'<div class="contact-panel-person">{contact_first_name}</div>' if contact_first_name else ""
    )
    contact_email_html = f'<div class="meta-line">{contact_email}</div>' if contact_email else ""
    # 2026-09-30, "simple letter-number matching": an uncomplicated
    # "Letter number: 1042" line -- only rendered when a real value was
    # actually passed in (see render_letter's own docstring on why None is
    # accepted at all).
    letter_number_html = (
        f'<div class="meta-line">Letter number: {int(letter_number)}</div>' if letter_number is not None else ""
    )
    # 2026-09-30, "optional contractor offer, for launch": the contractor's
    # own wording, shown compactly beside/below the contact panel -- gated
    # entirely on offer_text so "blank offer means nothing appears" (the
    # request's own wording) is literally true: no empty box, no filler
    # heading, nothing in the markup at all when offer_text is blank.
    # offer_code and offer_conditions are each independently optional too
    # (a contractor can give wording with no code, or no conditions).
    offer_text = esc(settings.offer_text)
    offer_code = esc(settings.offer_code)
    offer_conditions = esc(settings.offer_conditions)
    offer_code_html = f' <span class="offer-panel-code">Code: {offer_code}</span>' if offer_code else ""
    offer_conditions_html = (
        f'<div class="offer-panel-conditions">{offer_conditions}</div>' if offer_conditions else ""
    )
    offer_html = (
        f'<div class="offer-panel"><span class="offer-panel-label">Offer:</span> {offer_text}'
        f'{offer_code_html}{offer_conditions_html}</div>'
    ) if offer_text else ""
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
  <div class="address-clear-zone">
    <div class="meta-label">To the Property Owner / Occupier</div>
    <div class="recipient-address">{address_e}</div>
  </div>

  <div class="brand-header">
    <img class="brand-header-logo" src="{full_logo_uri}" alt="TreeKey">
    <div class="brand-header-tagline">
      <div>{BRAND_TAGLINE_LINE_1}</div>
      <div class="brand-header-tagline-sub">{BRAND_TAGLINE_LINE_2}</div>
    </div>
  </div>

  <div class="intro-ref-block">
    <div class="intro-ref-label">Your Introduction</div>
    <div class="meta-line">Reference: {lead_reference_e}</div>
    {letter_number_html}
    <div class="meta-line">{letter_date}</div>
  </div>

  <h1 class="letter-headline">{esc(FRONT_PAGE_HEADLINE)}</h1>

  <p class="body-text greeting-text">Dear homeowner,</p>
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
    <div class="contact-panel-business">{business_name}</div>
    {contact_first_name_html}
    <div class="contact-panel-phone">{phone}</div>
    {contact_email_html}
  </div>
  {offer_html}
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

  <div class="reserved-qr-area"></div>
  <div class="reserved-logo-area"></div>

  {_brand_footer_html(tk_mark_uri, page_number=1)}
</div>"""

    # Retention notice (8 Oct 2026): TreeKey's own deletion (72 hours, verified in code and tests) is kept
    # separate from the print provider's retention, which is only what Intelliprint confirmed in writing
    # (PDF kept 90 days from job confirmation; separate recipient and letter records have no automatic
    # deletion period and can be deleted on request, subject to legal requirements). No provider deletion
    # guarantee is claimed.
    reverse_page = f"""<div class="letter-page">
  <div class="reverse-header">
    <img class="reverse-header-mark" src="{icon_badge_uri}" alt="TreeKey">
    <div class="reverse-header-word">TREEKEY</div>
  </div>
  <div class="reverse-title">About this letter and your information</div>

  <div class="notice-section">
    <div class="notice-heading">Who is responsible?</div>
    <p class="notice-body">TreeKey is the trading name of Nicholas Michael Secular, a sole trader, who is
    responsible for your information. {write_or_email}</p>
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
    <p class="notice-body">About 72 hours after we record that this letter has been handed to Royal Mail,
    we delete your name, address and this letter's content from our own live systems. Our printing
    provider keeps a PDF of the letter for 90 days from when the print job is confirmed, and its separate
    recipient and letter records have no automatic deletion period; you can ask us to request their
    deletion, subject to legal requirements. A minimal record of the transaction (needed for accounting
    and to handle any complaint) is kept for longer; we have not yet set a fixed expiry for that minimal
    record.</p>
  </div>

  <div class="notice-section">
    <div class="notice-heading">Stop further marketing</div>
    <p class="notice-body">You have the right to object, at any time and without giving a reason, to TreeKey
    using your information for marketing. {objection_route}, and include the letter reference
    <b>{lead_reference_e}</b> if you have it (it is not required). We will stop using your information for
    TreeKey marketing and record your request. If a letter is already in production, it may still reach you.</p>
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
#
# 2026-09-26 fix -- no @page rule was ever declared here. Without one, an
# HTML-to-PDF engine falls back to ITS OWN default paper size and margins
# (commonly US Letter with a non-zero default margin on every side, e.g.
# ~1in/25.4mm) rather than the 210mm-wide, zero-margin page this document's
# own .letter-page div assumes. A .letter-page div that is exactly 210mm
# wide, centred with `margin: 0 auto`, only fits with no clipping if the
# actual print canvas is also exactly 210mm wide with zero page margin --
# otherwise the excess width has nowhere to go and the right edge (whichever
# side `auto`-centring doesn't push the overflow to) gets cut off. This is
# the most likely cause, by code inspection, of Nick's reported
# right-edge clipping; verify against Intelliprint's OWN rendering (not
# just this file's local preview) before treating it as fully confirmed --
# see the accompanying report. This @page rule does not change any
# dimension, margin value, or piece of content already in .letter-page --
# it only makes the actual print canvas match what that div already
# assumed, and it deliberately still says nothing about bleed/safe-zone
# insets (Intelliprint's documented 3mm-bleed/204x291mm-safe-zone figures
# are stated for pre-designed template ARTWORK uploads, not confirmed to
# apply to this letter's HTML/text `content` submission path -- see the
# report for why that wasn't assumed here either).
_LETTER_PAGE_CSS = f"""
@page {{ size: A4; margin: 0; }}
* {{ box-sizing: border-box; }}
/* 2026-09-27 fix ("restore the visual design"): TreeKey-short-review.pdf
   (Nick's supplied visual source of truth) renders the whole letter in a
   plain sans-serif face, not the serif (Georgia) this file had been using
   -- a real, visible typography difference, not a subjective read: compare
   the two side by side. Switched to a standard sans-serif stack (no
   specific named font was recoverable from a rendered PDF, so this is the
   closest safe match, not a confirmed exact font -- flagged as such in the
   report alongside this fix). This changes text metrics for every letter
   on the page, so tests/letter_pagination_check/run_pagination_check.py
   was re-run after this change specifically to confirm no edge case that
   previously fit in 2 pages now overflows to 3. */
body {{ margin: 0; padding: 0; background: #d9d9d9; font-family: Arial, Helvetica, "Nimbus Sans", sans-serif; }}
.letter-page {{
  width: 210mm; min-height: 297mm; margin: 0 auto; background: #fff; color: {BRAND_INK};
  /* 2026-09-27 fix ("restore the visual design"): bottom padding trimmed
     from 14mm to 10mm -- pure blank whitespace below the footer, not
     content -- to help make room for the restored headline (see
     .letter-headline below) without shrinking any text. See that rule's
     own comment for the full page-budget accounting.
     2026-09-27, header-resize pass (Nick's annotated-image request, green
     outline): top padding trimmed again, 16mm->12mm, to buy room for the
     taller .brand-header below without pushing its bottom edge into the
     measured address/barcode clearance zone (top=45.0mm) -- pure blank
     whitespace above the header, same "reclaim whitespace, never shrink
     text" rule as the earlier trim. This does NOT move
     .address-clear-zone: that element is position:absolute, positioned
     from .letter-page's own top edge (46mm/19mm), not from this padding,
     so it is unaffected by either page-padding trim -- confirmed via
     Playwright measurement, not assumed.
     Bottom padding trimmed again this same pass, 10mm->7mm: still well
     clear of Intelliprint's documented 3mm bleed margin (this module's
     own docstring/Intelliprint documentation findings), and needed as
     part of the same page-budget reclaim as .body-text/.disclaimer-text/
     .spec-box/.contact-panel above -- see run_pagination_check.py's
     re-verification after this whole pass's changes. */
  padding: 12mm 18mm 7mm 18mm; position: relative; line-height: 1.5;
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
/* 2026-09-27, header-resize pass: margin-bottom trimmed 10px->6px -- pure
   inter-paragraph whitespace, not text (font-size/line-height untouched) --
   as part of reclaiming the page-budget the taller .brand-header and the
   restructured .contact-panel (business name/first name/phone/email now
   on separate lines instead of one combined line) both spent. See
   run_pagination_check.py's own re-verification after this change. */
.body-text {{ font-size: 12.5px; text-align: justify; margin: 0 0 6px 0; }}
.meta-line {{ font-size: 11px; color: {BRAND_MUTED}; }}
.meta-label {{ font-size: 10px; letter-spacing: 0.04em; text-transform: uppercase; color: {BRAND_MUTED}; margin-bottom: 3px; }}
/* 2026-09-27, header-resize pass: margin trimmed 8px->5px (both sides),
   same page-budget reclaim described at .body-text above -- pure
   whitespace between disclaimer paragraphs, text itself unchanged. */
.disclaimer-text {{ font-size: 10px; line-height: 1.5; color: {BRAND_MUTED}; margin: 5px 0; }}

.brand-header {{
  background: {BRAND_DARK}; border-radius: 6px; padding: 16px 18px;
  /* 2026-09-27, header-resize pass (Nick's annotated-image request, green
     outline: "resize the banner to the indicated proportions, scaling its
     logo and text appropriately"). Measured the green outline against this
     header's own then-current rendered box in Nick's screenshot (PIL/numpy
     colour-mask bounding boxes, both converted through that screenshot's
     own measured page scale): width ratio ~1.03 (essentially unchanged --
     .letter-page's own 18mm side padding already fixes this header's
     width), height ratio ~1.586 (~59% taller). Applied that same ~1.586x
     factor to every dimension that makes up the header's height --
     vertical padding (10px->16px), .brand-header-logo's height (13mm->
     20.6mm), .brand-header-tagline's font-size (8px->12.5px) and
     .brand-header-tagline-sub's margin-top (2px->3px) -- rather than only
     the logo, so the resize reads as one proportional banner, not a
     stretched logo next to unchanged text, per "scaling its logo and text
     appropriately".
       This makes the header taller than .letter-page's top padding
     (16mm) + old header height (~18.3mm) previously left room for before
     hitting the measured Intelliprint zone (top=45.0mm) -- verified via
     Playwright, not assumed: at the old 16mm top padding this resized
     header's bottom edge lands at 34.3mm, comfortably clear once .letter-
     page's top padding is trimmed to 12mm (below) to buy the extra room,
     landing the header's bottom edge at 41.06mm -- a 3.94mm buffer before
     the zone, not a razor-thin one.
       margin-bottom trimmed 14mm->8mm as part of this same pass: the
     taller header pushes .intro-ref-block (and therefore .letter-headline,
     whose 31mm margin-top is relative to it -- see that rule's own
     comment) further down than before, which is safe for the zone's
     BOTTOM edge (90.3mm) but left an unnecessarily large buffer there
     (9.71mm, re-measured) -- reclaiming 6mm of that back via this margin
     lands the headline at top=94.01mm, a 3.71mm buffer, and recovers page-
     budget height the taller header otherwise would have spent (see
     .letter-headline's own comment for the full pagination accounting).
     This is the same margin this project was burned by trimming blindly
     once before (see version history / ERROR_LOG.md) -- the difference
     this time is it was trimmed by a computed amount, then re-verified via
     Playwright, not assumed safe. */
  margin-bottom: 8mm;
  margin-top: 0;
  display: flex; align-items: center; justify-content: space-between; gap: 12px;
}}
.brand-header-logo {{ height: 20.6mm; max-width: 60%; object-fit: contain; }}
.brand-header-tagline {{ color: #fff; font-size: 12.5px; font-weight: bold; letter-spacing: 0.03em; text-align: right; }}
.brand-header-tagline-sub {{ color: #A5E5CD; font-weight: normal; margin-top: 3px; }}

/* 2026-09-26 fix (ninth pass -- supersedes the previous top:20mm/left:40mm/
   width:120mm fix directly below this comment in version history).
     Nick's instruction: "your earlier '40mm from the left, 20mm from the
   top' interpretation may be reversed or otherwise inapplicable... Measure
   the actual provider output." That earlier figure came from a WebFetch
   text-summary of Intelliprint's A4_Template.pdf (a vector-graphic PDF
   WebFetch repeatedly struggled to extract precise coordinates from -- a
   previously-disclosed limitation) and turned out not to match reality:
   the real provider PDF (978ce488-preview_1.pdf, produced by the eighth
   fix's file-upload route) shows our address rendering ABOVE Intelliprint's
   own orange-outlined address/barcode guide box, which does not start
   until much further down the page.
     Measured DIRECTLY off that real PDF instead of trusting documentation
   a second time: rasterised page 1 at 200dpi (pdftoppm), isolated the
   orange outline by colour mask ((r>200)&(90<g<180)&(b<100), i.e.
   darkorange) and took its pixel bounding box, px->mm at 25.4/200:
     zone: left=18.5mm top=45.0mm right=106.9mm bottom=90.3mm
       (88.4mm x 45.3mm -- closely matches a standard C5/DL window-envelope
       address-window size, which is corroborating evidence the measurement
       is right, not an artifact).
   Then isolated Intelliprint's own barcode within that zone the same way
   (near-black pixel mask, restricted to below the header's contamination
   band so header pixels couldn't be mistaken for it -- confirmed visually
   first via a cropped render before trusting the numeric mask):
     barcode: left=77.1mm right=89.8mm top=69.5mm bottom=82.2mm
       (a ~12.7mm square sitting in the zone's right-centre -- consistent
       with a Royal Mail Mailmark-style 2D datamatrix).
   This leaves the zone's left/upper area (x:18.5-77.1mm, all of
   y:45.0-90.3mm) clear for our own address text. Positioned with a small
   inward buffer from the measured edges so rounding/anti-aliasing in the
   measurement can't put us back outside the real zone, and a width that
   stops comfortably short of the barcode's left edge (74mm right edge vs
   the barcode's measured 77.1mm -- ~3mm clearance):
     top:46mm left:19mm width:55mm
   Whether the orange outline itself is print-visible ink or a preview-only
   diagnostic guide is NOT resolved by this fix (see the accompanying
   report) -- but that doesn't change what to do here: the barcode is
   printed either way, and the zone is exactly where Intelliprint's OCR
   reads the address from (confirmed by the eighth fix's own finding that
   file-based submissions read the address off the page), so staying inside
   it is required regardless of whether its outline itself prints.
   Absolutely positioned (unchanged from the earlier fix) so it lands at
   these exact coordinates from the physical page edge regardless of what
   else is on the page; .letter-page has no border, so position:absolute's
   offsets are still relative to the page's own physical edge. Pending
   confirmation against a fresh real Intelliprint submission before being
   called fixed -- per Nick's own explicit instruction, a local render is
   not sufficient evidence for this specific claim. */
.address-clear-zone {{ position: absolute; top: 46mm; left: 19mm; width: 55mm; }}
.recipient-address {{ font-size: 12.5px; font-weight: bold; white-space: pre-line; }}
.intro-ref-block {{ text-align: right; margin-bottom: 4mm; }}
/* 2026-09-26 fix (ninth pass) / 2026-09-27 fix (restoring the headline,
   continued). .address-clear-zone above is position:absolute, so it does
   NOT push flowed content down by its own height -- confirmed by
   measuring the flow with Playwright's getBoundingClientRect BEFORE this
   rule existed: the first full-width flowed element after
   .intro-ref-block started at y=66.24mm, deep inside the real
   45.0-90.3mm zone measured off Nick's actual Intelliprint PDF, and would
   have spanned the zone's whole x-range too -- exactly the
   "branding/body text outside reserved areas" failure Nick's instruction
   called out, independent of whether it visually collided with our own
   address text (it starts below that, so it wouldn't have). This
   margin-top is sized to clear the zone's bottom (90.3mm) with a buffer.
     First attempt (26mm) was sized assuming plain addition
   (62.24mm .intro-ref-block bottom + 4mm its own margin-bottom + 26mm =
   92.24mm) but re-measuring with getBoundingClientRect after adding it
   showed the next element actually starting at 88.24mm -- still 2mm
   INSIDE the zone. Cause: adjoining vertical margins collapse in normal
   flow, so .intro-ref-block's 4mm margin-bottom and this rule's
   margin-top don't add, only the larger of the two applies (62.24 + 26 =
   88.24, matching what was measured). Caught by re-measuring rather than
   trusting the arithmetic -- the "measure, don't assume" rule applies to
   CSS mechanics here too, not just to Intelliprint's own specs.
     Raised to 31mm: expected/measured top = 93.24mm, ~3mm clear of the
   zone's 90.3mm bottom edge. Re-ran run_pagination_check.py after adding
   this to confirm the extra space does not push any validate()-accepting
   edge case to a 3rd page.
     2026-09-27: this rule moved from .greeting-text to .letter-headline
   below, because the restored headline (TreeKey-short-review.pdf) is now
   the element directly after .intro-ref-block in source order -- it,
   not the greeting, is what needs to clear the zone now. Re-measured
   after moving it: same 93.24mm result, since it's the same sibling
   relationship the arithmetic above already covers. .greeting-text keeps
   a small margin-top of its own, just for normal paragraph spacing under
   the headline, not zone clearance. */
/* 2026-09-27 fix ("restore the visual design"), page-budget accounting:
   restoring this headline (a real, ~7mm-tall new line of content, not
   just whitespace) plus switching the base font to sans-serif (which
   wraps some long-field edge cases onto more lines than Georgia did)
   pushed the existing 'max_length_within_combined_budget' edge case --
   the realistic worst case a real contractor's own saved settings could
   actually reach, see run_pagination_check.py's own comment on it -- from
   2 pages to 3 (measured via Playwright: front page content totalled
   305.46mm against the 297mm A4 budget, ~8.5mm over). Not fixed by
   shrinking the headline's own text or line-height (this codebase's
   long-standing rule: never shrink text to force overflow to fit --
   see run_pagination_check.py's own docstring).
     IMPORTANT constraint discovered while fixing this: whitespace ABOVE
   this rule's 31mm margin-top (e.g. .brand-header's own margin-bottom)
   cannot be trimmed without a cost -- this margin-top is relative to
   whatever immediately precedes it in flow, so shrinking space upstream
   of it shifts this headline's absolute page position up by the same
   amount, eating straight back into the Intelliprint zone clearance it
   exists to provide (tried trimming .brand-header's margin-bottom first;
   caught by re-measuring that it pushed this headline back to
   top=87.24mm, inside the zone; reverted -- see that rule's own comment).
   Only whitespace DOWNSTREAM of this headline reclaims cleanly without
   touching the zone clearance. Trimmed: .letter-page's bottom padding
   (14mm->10mm, 4mm), this rule's own margin-bottom (6mm->2mm, 4mm), and
   .contact-panel's margin (14px->8px top+bottom, ~3.2mm) -- 11.2mm
   combined, a ~2.7mm buffer over the ~8.5mm overage rather than a
   razor-thin one (the sixth-pass fix's own history, a previous
   ~1.3mm-clearance decision, is exactly the kind of thin margin this
   project has already been burned by once). Re-ran
   run_pagination_check.py after these trims: every validate()-accepting
   edge case is back to 2 pages across all 3 templates, including this
   one. None of this touches the 31mm top-margin itself, which is the
   empirically-measured Intelliprint zone clearance and stays exactly as
   measured. */
.letter-headline {{
  font-size: 21px; font-weight: bold; color: {BRAND_DARK}; margin: 31mm 0 2mm 0;
  line-height: 1.25;
}}
.greeting-text {{ margin-top: 0; }}
.intro-ref-label {{ font-size: 10.5px; font-weight: bold; color: {BRAND_GREEN}; margin-bottom: 4px; }}

/* 2026-09-27, header-resize pass: margin trimmed 12px->8px, same
   page-budget reclaim as .body-text/.disclaimer-text above. */
.spec-box {{ background: {BRAND_PALE}; border-left: 3px solid {BRAND_GREEN}; padding: 10px 14px; margin: 8px 0; font-size: 12px; }}

/* margin trimmed from 14px to 8px top+bottom as part of the page-budget
   accounting in .letter-headline's comment above -- pure whitespace
   downstream of the restored headline/Intelliprint-zone clearance, safe
   to reclaim without affecting either.
   2026-09-27, header-resize pass: margin trimmed again, 8px->4px, and
   vertical padding 12px->10px -- the panel itself now holds more content
   (business name, optional first name, phone, optional email each on
   their own line -- see contact_first_name_html/contact_email_html in
   render_letter) than the single combined line it replaced, so its own
   whitespace is trimmed to help offset that, same page-budget reclaim as
   .body-text/.disclaimer-text/.spec-box above. */
.contact-panel {{ background: {BRAND_PALE}; border-left: 3px solid {BRAND_GREEN}; border-radius: 4px; padding: 10px 16px; margin: 4px 0; }}
.contact-panel-label {{ font-size: 10.5px; font-weight: bold; color: {BRAND_GREEN}; letter-spacing: 0.02em; text-transform: uppercase; margin-bottom: 6px; }}
/* 2026-09-27, contact panel redesign (Nick's annotated-image request, red
   circle: "prominently show the contractor's company name, contact's first
   name if supplied, and telephone number"). Business name now gets its own
   prominent line (previously folded into a small .meta-line alongside the
   email address); contact-panel-person is genuinely optional and only
   rendered at all when a first name is actually saved (see render_letter's
   contact_first_name_html) -- never a placeholder. */
.contact-panel-business {{ font-size: 15px; font-weight: bold; color: {BRAND_DARK}; margin-bottom: 2px; }}
.contact-panel-person {{ font-size: 12px; color: {BRAND_INK}; margin-bottom: 4px; }}
.contact-panel-phone {{ font-size: 17px; font-weight: bold; color: {BRAND_DARK}; margin-bottom: 4px; }}

/* 2026-09-30, optional contractor offer: compact, sits directly below the
   contact panel -- gated entirely on offer_text in render_letter (see
   offer_html there), so this rule is present in every stylesheet but
   produces nothing on the page when a contractor has no offer saved (no
   empty box, no filler heading). Kept deliberately plain/small (11.5px, no
   background or border of its own) so it reads as a small addition to the
   contact panel above it, not a second competing panel -- and short by
   MAX_OFFER_TEXT_LEN/MAX_OFFER_CODE_LEN/MAX_OFFER_CONDITIONS_LEN construction. */
.offer-panel {{ font-size: 11.5px; color: {BRAND_INK}; margin: 2px 0 4px 0; }}
.offer-panel-label {{ font-weight: bold; color: {BRAND_GREEN}; }}
.offer-panel-code {{ font-weight: bold; }}
.offer-panel-conditions {{ font-size: 9.5px; color: #333D38; margin-top: 1px; }}

.reverse-header {{ display: flex; align-items: center; gap: 8px; margin-bottom: 4mm; }}
.reverse-header-mark {{ height: 7mm; width: 7mm; object-fit: contain; }}
.reverse-header-word {{ font-size: 12px; font-weight: bold; letter-spacing: 0.04em; color: {BRAND_DARK}; }}
.reverse-title {{ font-size: 14px; font-weight: bold; margin-bottom: 8mm; color: {BRAND_INK}; }}

.notice-section {{ margin-bottom: 9px; }}
.notice-heading {{ font-size: 10.5px; font-weight: bold; color: {BRAND_INK}; margin-bottom: 2px; }}
.notice-body {{ font-size: 9.5px; line-height: 1.45; color: #333D38; margin: 0; }}

/* 2026-09-27, Nick's annotated-image request (orange + blue areas: "leave
   blank for now") plus the two matching to-do-list additions ("the reserved
   orange area" for a future QR code, "the reserved blue area" for a future
   contractor logo/advertising image). Genuinely reserved page real estate,
   not yet-rendered content -- so, same technique as .address-clear-zone
   above: position:absolute, taken entirely out of normal flow. That makes
   this pass's own "preserve the two-page layout" requirement automatic
   (an empty, transparent, absolutely-positioned box cannot push anything
   onto a third page, however tall it is) and safe against the front page's
   variable content length (a maximum-length letter's flowed disclaimer
   text may run underneath these boxes rather than clear of them, but since
   neither box has a background or border -- "without a visible border" is
   Nick's own wording for the QR box, kept consistent for both -- an
   overlap is invisible, not a rendering defect).
     Position/size approximated from Nick's own annotated screenshot
   (PIL/numpy colour-mask bounding boxes, converted from screenshot pixels
   to page mm via that screenshot's own measured page edges/scale -- left
   edge at x=39px, top edge at y=43px, ~2.457px/mm both axes, cross-checked
   against the page's known 210x297mm size on two independent axes).
   Nick's own drawn boxes are a rough visual indication of roughly where
   and how large ("small" / "larger"), not a stated exact spec -- flagged
   in the handoff as an approximation, to be refined once the QR-code/
   logo-image features are actually built (the to-do items above), not a
   number Nick dictated. */
.reserved-qr-area {{ position: absolute; left: 20mm; top: 203mm; width: 25mm; height: 22mm; }}
.reserved-logo-area {{ position: absolute; left: 52mm; top: 201mm; width: 133mm; height: 73mm; }}

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
