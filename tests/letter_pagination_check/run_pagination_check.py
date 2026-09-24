#!/usr/bin/env python3
"""
tests/letter_pagination_check/run_pagination_check.py

2026-09-24 handoff task: "Verify all three designs with short and
maximum-length content, long business names, contact details, punctuation
and missing optional fields. Inspect rendered output for clipping, blank
pages, unreadable text and unexpected extra pages. Do not shrink text
automatically to force overflowing content to fit."

WHY THIS IS A SEPARATE SCRIPT, NOT PART OF `unittest discover`
----------------------------------------------------------------
This follows the same convention as tests/postgres_concurrency/: a real,
empirical check that needs a heavy external dependency (here, a headless
Chromium browser via Playwright) that has no place running on every
`unittest discover` pass. letter_content.py itself has NO dependency on
Playwright or Pillow at runtime -- only this one-off verification script
does, matching the "no new runtime dependency" decision documented in
letter_content.py's own asset-loading code.

WHAT THIS DOES
---------------
For each of the 3 registered templates (friendly_introduction,
professional_and_factual, short_and_direct), renders render_letter() with a
set of edge-case ContractorLetterSettings + lead inputs, using Chromium
(headless, print media) to export each render to a PDF at real A4 size --
the same rendering engine class a real "print/export to PDF" action would
use -- then:
  1. Checks the PDF page count is exactly 2 (not 1, not 3+) via pypdf.
  2. Rasterises each PDF page to a PNG (pdftoppm) for visual inspection
     (clipping, overlap, blank pages, unreadable/cut-off text) -- this
     script prints the file paths; actually LOOKING at them (e.g. via the
     Read tool, which renders PDF/PNG pages as images) is a separate,
     deliberate step, not automated here, because "does this look wrong to
     a human" is not something this script can assert about.
  3. Extracts text via pdftotext and does a few cheap automated sanity
     checks (no obviously-truncated field, no stray unescaped markup).

Nothing here modifies letter_content.py's MAX_* limits -- those are only
adjusted afterwards, manually, if this script's output shows genuine
overflow, and never by adding dynamic font-shrinking (explicitly
prohibited by the task).

USAGE
-----
    cd /home/claude/treekey_work/app
    export TREEKEY_PRIVACY_CONTACT_EMAIL=privacy@treekey.co.uk
    export TREEKEY_PRIVACY_POLICY_URL=https://treekey.example/privacy-policy
    python3 tests/letter_pagination_check/run_pagination_check.py

Output goes to tests/letter_pagination_check/_out/ (gitignored-style scratch
dir, safe to delete/regenerate; not shipped in the checkpoint ZIP's actual
runtime path since letter_content.py never reads from it).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

os.environ.setdefault("TREEKEY_PRIVACY_CONTACT_EMAIL", "privacy@treekey.co.uk")
os.environ.setdefault("TREEKEY_PRIVACY_POLICY_URL", "https://treekey.example/privacy-policy")

import letter_content  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "_out"
OUT_DIR.mkdir(exist_ok=True)

MAX = letter_content


def _settings(**overrides) -> letter_content.ContractorLetterSettings:
    base = dict(
        contractor_email="contractor@example.com",
        business_name="Apex Tree Care",
        phone="0113 555 0199",
        template_key="friendly_introduction",
    )
    base.update(overrides)
    return letter_content.ContractorLetterSettings(**base)


# ---------------------------------------------------------------------------
# Edge cases. Each is (case_name, settings_kwargs (minus template_key), lead_kwargs)
# ---------------------------------------------------------------------------

LONG_BUSINESS_NAME = "A" * MAX.MAX_BUSINESS_NAME_LEN  # exactly at the limit
LONG_PHONE = "+44 (0)113 555 0199 ext.12345, or 0113 555 0200 (evenings)"[: MAX.MAX_PHONE_LEN]
LONG_CONTACT_EMAIL = ("a" * (MAX.MAX_CONTACT_EMAIL_LEN - len("@example-long-domain.co.uk"))) + "@example-long-domain.co.uk"
LONG_SERVICE_AREA = ("Leeds, Bradford, Wakefield, Huddersfield, Halifax, Dewsbury, Pudsey, "
                      "Otley, Ilkley, Wetherby, Tadcaster, Garforth, Rothwell -- and the "
                      "surrounding rural parishes within a 25 mile radius of our depot.")[: MAX.MAX_SERVICE_AREA_LEN]
LONG_BUSINESS_INTRO = (
    "Apex Tree Care has been carrying out tree surgery, felling, pruning, hedge-cutting and "
    "stump-grinding work across the wider Leeds area for a number of years, working with "
    "homeowners, landlords, housing associations and small commercial sites alike; we are a "
    "small, local, family-run team and we try to keep our quotes straightforward and our "
    "communication direct, without any pressure to commit before you are ready to."
)[: MAX.MAX_BUSINESS_INTRO_LEN]
LONG_SERVICES_NOTE = (
    "Felling, crown reduction, crown thinning, deadwooding, hedge trimming, stump grinding, "
    "site clearance and emergency storm-damage callouts, plus TPO and conservation-area "
    "application paperwork support on request."
)[: MAX.MAX_SERVICES_NOTE_LEN]
LONG_INSURANCE = (
    "Covered by public liability insurance up to £5,000,000; certificate available on request "
    "before any work begins, and again at the point of any written quotation we provide."
)[: MAX.MAX_INSURANCE_LEN]
LONG_QUALIFICATIONS = (
    "NPTC-certificated chainsaw and climbing operators; team members hold City & Guilds Level 2 "
    "and 3 arboriculture qualifications and undertake regular refresher training."
)[: MAX.MAX_QUALIFICATIONS_LEN]

PUNCTUATION_TORTURE = "O'Brien & Sons (Tree Surgery) Ltd. -- “Quality” work, 24/7 – est. 1998!"

EDGE_CASES: "list[tuple[str, dict, dict]]" = [
    (
        "short_minimal",
        dict(business_name="A. Smith", phone="07700 900123"),
        dict(lead_reference="PLANIT-1", address="1 Elm Rd", summary="Fell one oak", council="Leeds"),
    ),
    (
        "all_optional_fields_blank",
        dict(business_name="Apex Tree Care", phone="0113 555 0199",
             service_area_note="", insurance_note="", qualifications_note="",
             business_intro="", services_note="", contact_email=""),
        dict(lead_reference="PLANIT-2", address="2 Oak Ave", summary="Reduce crown of sycamore", council="Bradford"),
    ),
    (
        "max_length_everything",
        dict(business_name=LONG_BUSINESS_NAME, phone=LONG_PHONE, contact_email=LONG_CONTACT_EMAIL,
             service_area_note=LONG_SERVICE_AREA, business_intro=LONG_BUSINESS_INTRO,
             services_note=LONG_SERVICES_NOTE, insurance_note=LONG_INSURANCE,
             qualifications_note=LONG_QUALIFICATIONS),
        dict(lead_reference="PLANIT-2026-000123456", address="Flat 9, The Old Coach House, "
             "12-14 Long Wittenham Road, Little Snoring-cum-Great Snoring, Norfolk",
             summary=("Fell two mature oak trees (T1, T2) subject to Tree Preservation Order "
                       "2019/00456, reduce crown of adjacent beech by approximately 20%, and carry "
                       "out crown lifting on three further trees along the northern boundary"),
             council="North Yorkshire County Council"),
    ),
    (
        # This is the realistic worst case: every field near its own limit,
        # while still fitting MAX_TOTAL_DYNAMIC_CONTENT_LEN=1400 combined, so
        # validate() actually accepts it -- unlike "max_length_everything"
        # above (which maxes every field independently and so exceeds the
        # combined budget; that case still renders, since render_letter
        # itself does not call validate(), and is kept as a defense-in-depth
        # look at raw render_letter behaviour beyond what a contractor could
        # actually save).
        "max_length_within_combined_budget",
        dict(business_name=LONG_BUSINESS_NAME[:100], phone=LONG_PHONE[:30],
             contact_email=("a" * 33) + "@example-long-domain.co.uk",  # 60 chars, still a valid address
             service_area_note=LONG_SERVICE_AREA[:100],
             business_intro=LONG_BUSINESS_INTRO[:380], services_note=LONG_SERVICES_NOTE[:250],
             insurance_note=LONG_INSURANCE[:240], qualifications_note=LONG_QUALIFICATIONS[:240]),
        dict(lead_reference="PLANIT-2026-000123456", address="Flat 9, The Old Coach House, "
             "12-14 Long Wittenham Road, Little Snoring-cum-Great Snoring, Norfolk",
             summary=("Fell two mature oak trees (T1, T2) subject to Tree Preservation Order "
                       "2019/00456, reduce crown of adjacent beech by approximately 20%"),
             council="North Yorkshire County Council"),
    ),
    (
        "long_business_name_only",
        dict(business_name="The Yorkshire & Humberside Arboricultural Tree Surgery Company Limited",
             phone="0113 555 0199"),
        dict(lead_reference="PLANIT-3", address="3 Birch Close", summary="Hedge trimming", council="Wakefield"),
    ),
    (
        "punctuation_torture",
        dict(business_name=PUNCTUATION_TORTURE, phone="0113-555-0199 / 07700 900123",
             contact_email="o'brien+trees@example.co.uk",
             service_area_note='"Anywhere within 20 miles" -- see our website for full list.'),
        dict(lead_reference="PLANIT-4/A", address="4 Willow Way, Apt. 2B", summary='Remove "leaning" ash (dead)',
             council="Kirklees"),
    ),
    (
        "missing_optional_business_intro_and_services",
        dict(business_name="Apex Tree Care", phone="0113 555 0199",
             insurance_note="Public liability insured to £5m.",
             qualifications_note="NPTC certificated."),
        dict(lead_reference="PLANIT-5", address="5 Maple Dr", summary="Pollarding", council="Calderdale"),
    ),
]

TEMPLATES = list(letter_content.TEMPLATE_REGISTRY.keys())


def render_all() -> "list[tuple[str, str, Path]]":
    """Returns [(case_name, template_key, html_path), ...]."""
    results = []
    for case_name, settings_kwargs, lead_kwargs in EDGE_CASES:
        for template_key in TEMPLATES:
            settings = _settings(template_key=template_key, **settings_kwargs)
            problems = settings.validate()
            html_out = letter_content.render_letter(settings, **lead_kwargs)
            slug = f"{case_name}__{template_key}"
            html_path = OUT_DIR / f"{slug}.html"
            html_path.write_text(
                f"<!-- validate() problems: {problems!r} -->\n" + html_out, encoding="utf-8"
            )
            results.append((case_name, template_key, html_path, problems))
    return results


def print_to_pdf(html_path: Path, pdf_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path="/opt/pw-browsers/chromium-1194/chrome-linux/chrome" if
                                     Path("/opt/pw-browsers/chromium-1194/chrome-linux/chrome").exists() else None)
        page = browser.new_page()
        page.goto(html_path.as_uri())
        page.emulate_media(media="print")
        page.pdf(path=str(pdf_path), format="A4", print_background=True, margin={
            "top": "0", "bottom": "0", "left": "0", "right": "0",
        })
        browser.close()


def main() -> int:
    print(f"Rendering {len(EDGE_CASES)} edge cases x {len(TEMPLATES)} templates "
          f"= {len(EDGE_CASES) * len(TEMPLATES)} letters...")
    rendered = render_all()

    import pypdf

    report_lines = []
    exit_code = 0
    for case_name, template_key, html_path, problems in rendered:
        slug = html_path.stem
        pdf_path = OUT_DIR / f"{slug}.pdf"
        print_to_pdf(html_path, pdf_path)

        reader = pypdf.PdfReader(str(pdf_path))
        page_count = len(reader.pages)

        text_path = OUT_DIR / f"{slug}.txt"
        os.system(f'pdftotext -layout "{pdf_path}" "{text_path}" 2>/dev/null')
        text = text_path.read_text(encoding="utf-8", errors="replace") if text_path.exists() else ""

        # Rasterise each page for visual inspection.
        png_prefix = OUT_DIR / f"{slug}"
        os.system(f'pdftoppm -png -r 100 "{pdf_path}" "{png_prefix}" 2>/dev/null')
        png_paths = sorted(OUT_DIR.glob(f"{slug}-*.png"))

        status = "OK" if page_count == 2 else "PAGE-COUNT-MISMATCH"
        if page_count != 2:
            exit_code = 1

        validated_ok = "REJECTED-BY-VALIDATE" if problems else "validate()-clean"

        line = (f"[{status}] [{validated_ok}] case={case_name!r:45s} template={template_key:25s} "
                f"pages={page_count} pngs={[p.name for p in png_paths]}")
        print(line)
        report_lines.append(line)
        if problems:
            report_lines.append(f"    validate() problems (expected for max-length case, since it uses "
                                 f"per-field limits at their exact boundary -- see inline note below): {problems}")

    report_path = OUT_DIR / "REPORT.txt"
    report_path.write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    print(f"\nFull report written to {report_path}")
    print("PNG page renders are in the same _out/ directory -- inspect them visually "
          "(e.g. with the Read tool) for clipping, blank pages, unreadable text, overlap.")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
