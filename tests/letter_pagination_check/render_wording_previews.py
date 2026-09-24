#!/usr/bin/env python3
"""
tests/letter_pagination_check/render_wording_previews.py

Renders all three registered templates through render_preview_letter() --
the EXACT SAME function/fixed fictional sample data
(letter_content.PREVIEW_LEAD_REFERENCE/PREVIEW_ADDRESS/PREVIEW_SUMMARY/
PREVIEW_COUNCIL) a real contractor sees on the letter-settings approval
screen -- so these previews are provably what Nick is being asked to
wording-approve, not a separate one-off rendering.

2026-09-24 handoff: "give previews of all three latest letters for wording
approval... the layout is approved but wording approval is still
outstanding -- keep that distinction clear." This script exists ONLY to
produce those PDFs; it does not touch letter_content.py, does not change
any template wording, and reuses render_preview_letter() rather than
building a second preview path.

Uses a single, clearly-fictional example ContractorLetterSettings (same
"Apex Tree Care" example already used throughout tests/test_letter_content.py
and tests/letter_pagination_check/run_pagination_check.py -- reused for
consistency, not invented fresh here) with every optional field filled in,
so the reviewer sees the fullest realistic version of each template, not a
sparse minimal one.

Usage:
    cd /home/claude/treekey_work/app
    export TREEKEY_PRIVACY_CONTACT_EMAIL=privacy@treekey.co.uk
    export TREEKEY_PRIVACY_POLICY_URL=https://treekey.example/privacy-policy
    python3 tests/letter_pagination_check/render_wording_previews.py

Output: tests/letter_pagination_check/_out/wording_preview_<template>.pdf
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

EXAMPLE_SETTINGS_KWARGS = dict(
    contractor_email="contractor@example.com",
    business_name="Apex Tree Care",
    phone="0113 555 0199",
    contact_email="hello@apextreecare.example",
    service_area_note="Leeds, Bradford, Wakefield and surrounding areas",
    business_intro=(
        "Apex Tree Care is a small, local, family-run team carrying out tree surgery, felling, "
        "pruning and hedge-cutting work across the wider Leeds area."
    ),
    services_note="Felling, crown reduction, hedge trimming, stump grinding and emergency storm-damage callouts.",
    insurance_note="Covered by public liability insurance up to £5,000,000; certificate available on request.",
    qualifications_note="NPTC-certificated chainsaw and climbing operators.",
)


def print_to_pdf(html_path: Path, pdf_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(html_path.as_uri())
        page.emulate_media(media="print")
        page.pdf(path=str(pdf_path), format="A4", print_background=True,
                 margin={"top": "0", "bottom": "0", "left": "0", "right": "0"})
        browser.close()


def main() -> int:
    for template_key, template in letter_content.TEMPLATE_REGISTRY.items():
        settings = letter_content.ContractorLetterSettings(
            template_key=template_key, **EXAMPLE_SETTINGS_KWARGS,
        )
        html_out = letter_content.render_preview_letter(settings)
        slug = f"wording_preview_{template_key}"
        html_path = OUT_DIR / f"{slug}.html"
        html_path.write_text(html_out, encoding="utf-8")
        pdf_path = OUT_DIR / f"{slug}.pdf"
        print_to_pdf(html_path, pdf_path)
        print(f"[{template_key}] label={template.label!r} version={template.version} -> {pdf_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
