"""
test_letter_content.py -- letter_content.py's shared render function,
template approval, and fingerprint consistency.

2026-09-23 handoff additions (contractor letter-template selector +
constrained editor): TestTemplateSelection, TestNoAutoAddedClaimWords,
TestLengthLimitsAndContentBudget, TestRejectsMarkupInjection,
TestEscapingDefenseInDepth, TestNewEditableFieldsRenderWhenPresent, and the
new cases in TestFingerprintConsistency below.

Run with:
    python -m unittest tests.test_letter_content -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import letter_content


class FakeCursor:
    def __init__(self, fetchone_results=None):
        self.executed = []
        self._fetchone_results = list(fetchone_results or [])

    def execute(self, sql, params=None):
        self.executed.append((sql.strip(), params))

    def fetchone(self):
        return self._fetchone_results.pop(0) if self._fetchone_results else None


def _settings(**overrides):
    base = dict(contractor_email="contractor@example.com", business_name="Apex Tree Care",
                phone="0113 000 0000")
    base.update(overrides)
    return letter_content.ContractorLetterSettings(**base)


class TestConfigIsNeverGuessed(unittest.TestCase):
    def test_missing_privacy_contact_email_raises_loudly(self):
        """Section 5: 'Verify configuration rather than guessing which
        email address is operational.' The letter must refuse to render
        rather than pick one of the two historical, inconsistent addresses
        found this session (contact@treekey.co.uk vs contact@treekey.uk)."""
        os.environ.pop(letter_content.PRIVACY_CONTACT_EMAIL_ENV, None)
        with self.assertRaises(letter_content.LetterConfigError):
            letter_content.render_letter(_settings(), lead_reference="PLANIT-001",
                                          address="1 Test St", summary="Fell one oak", council="Leeds")

    def test_renders_once_configured(self):
        os.environ[letter_content.PRIVACY_CONTACT_EMAIL_ENV] = "privacy@treekey.co.uk"
        html = letter_content.render_letter(_settings(), lead_reference="PLANIT-001",
                                             address="1 Test St", summary="Fell one oak", council="Leeds")
        self.assertIn("privacy@treekey.co.uk", html)
        self.assertIn("Apex Tree Care", html)


class TestNoInventedClaims(unittest.TestCase):
    def test_no_insurance_note_means_no_insurance_claim_in_output(self):
        """Section 5: 'Do not invent qualifications, insurance, approvals
        or testimonials.' A contractor who left insurance_note blank must
        get a letter with NO insurance claim, not a TreeKey-fabricated
        default like the old hardcoded '£5,000,000 Public Liability
        Insurance' line."""
        os.environ[letter_content.PRIVACY_CONTACT_EMAIL_ENV] = "privacy@treekey.co.uk"
        html = letter_content.render_letter(_settings(insurance_note=""), lead_reference="PLANIT-001",
                                             address="1 Test St", summary="Fell one oak", council="Leeds")
        self.assertNotIn("5,000,000", html)
        self.assertNotIn("Public Liability", html)

    def test_contractor_supplied_insurance_note_is_included_verbatim(self):
        os.environ[letter_content.PRIVACY_CONTACT_EMAIL_ENV] = "privacy@treekey.co.uk"
        html = letter_content.render_letter(
            _settings(insurance_note="Insured up to £2,000,000 (policy on request)."),
            lead_reference="PLANIT-001", address="1 Test St", summary="Fell one oak", council="Leeds")
        self.assertIn("Insured up to £2,000,000", html)


class TestFingerprintConsistency(unittest.TestCase):
    def test_same_inputs_produce_same_fingerprint(self):
        os.environ[letter_content.PRIVACY_CONTACT_EMAIL_ENV] = "privacy@treekey.co.uk"
        html1 = letter_content.render_letter(_settings(), lead_reference="PLANIT-001",
                                              address="1 Test St", summary="Fell one oak", council="Leeds")
        html2 = letter_content.render_letter(_settings(), lead_reference="PLANIT-001",
                                              address="1 Test St", summary="Fell one oak", council="Leeds")
        self.assertEqual(letter_content.content_fingerprint(html1), letter_content.content_fingerprint(html2))

    def test_different_settings_produce_different_fingerprint(self):
        os.environ[letter_content.PRIVACY_CONTACT_EMAIL_ENV] = "privacy@treekey.co.uk"
        html1 = letter_content.render_letter(_settings(business_name="Apex Tree Care"), lead_reference="PLANIT-001",
                                              address="1 Test St", summary="Fell one oak", council="Leeds")
        html2 = letter_content.render_letter(_settings(business_name="Different Tree Co"), lead_reference="PLANIT-001",
                                              address="1 Test St", summary="Fell one oak", council="Leeds")
        self.assertNotEqual(letter_content.content_fingerprint(html1), letter_content.content_fingerprint(html2))


class TestApprovalTracksFingerprint(unittest.TestCase):
    def test_settings_with_no_saved_row_cannot_be_approved(self):
        cur = FakeCursor(fetchone_results=[None])
        with self.assertRaises(letter_content.LetterConfigError):
            letter_content.approve_template(cur, "contractor@example.com", preview_fingerprint="abc123")

    def test_is_approval_current_true_only_when_fingerprint_matches(self):
        approved = _settings(approved=True, approved_fingerprint="abc123")
        self.assertTrue(letter_content.is_approval_current(approved, "abc123"))
        self.assertFalse(letter_content.is_approval_current(approved, "different-fingerprint"))

    def test_unapproved_settings_are_never_current(self):
        not_approved = _settings(approved=False, approved_fingerprint=None)
        self.assertFalse(letter_content.is_approval_current(not_approved, "abc123"))

    def test_saving_settings_always_resets_approval(self):
        """Section 5: a material change requires renewed approval. Asserts
        the SQL literally forces approved=FALSE on every save, so a
        contractor editing their phone number can't silently keep
        'approved' status against genuinely different content."""
        cur = FakeCursor()
        letter_content.upsert_contractor_settings(cur, _settings())
        sql, params = cur.executed[0]
        self.assertIn("approved = FALSE", sql)


class TestValidation(unittest.TestCase):
    def test_missing_business_name_is_invalid(self):
        problems = _settings(business_name="").validate()
        self.assertIn("business_name is required", problems)

    def test_missing_phone_is_invalid(self):
        problems = _settings(phone="").validate()
        self.assertIn("phone is required", problems)

    def test_upsert_raises_on_invalid_settings(self):
        cur = FakeCursor()
        with self.assertRaises(ValueError):
            letter_content.upsert_contractor_settings(cur, _settings(business_name=""))


class TestTemplateSelection(unittest.TestCase):
    """2026-09-23 handoff: the three-template selector."""

    def setUp(self):
        os.environ[letter_content.PRIVACY_CONTACT_EMAIL_ENV] = "privacy@treekey.co.uk"

    def test_registry_has_exactly_the_three_requested_templates(self):
        self.assertEqual(
            set(letter_content.TEMPLATE_REGISTRY.keys()),
            {"friendly_introduction", "professional_and_factual", "short_and_direct"},
        )
        labels = {t.label for t in letter_content.TEMPLATE_REGISTRY.values()}
        self.assertEqual(labels, {"Friendly Introduction", "Professional and Factual", "Short and Direct"})

    def test_default_template_key_is_valid(self):
        self.assertIn(letter_content.DEFAULT_TEMPLATE_KEY, letter_content.TEMPLATE_REGISTRY)
        self.assertEqual(_settings().template_key, letter_content.DEFAULT_TEMPLATE_KEY)

    def test_unknown_template_key_is_rejected_by_validate(self):
        problems = _settings(template_key="made_up_template").validate()
        self.assertTrue(any("template_key" in p for p in problems))

    def test_each_registered_template_renders_successfully_and_distinctly(self):
        rendered = {}
        for key in letter_content.TEMPLATE_REGISTRY:
            html_out = letter_content.render_letter(
                _settings(template_key=key), lead_reference="PLANIT-001",
                address="1 Test St", summary="Fell one oak", council="Leeds",
            )
            rendered[key] = html_out
        # Every template must actually produce different wording -- a
        # selector that always rendered the same shell regardless of
        # selection would defeat the whole feature.
        self.assertEqual(len(set(rendered.values())), len(rendered))

    def test_locked_reverse_page_is_identical_across_every_template(self):
        """Section: 'Keep TreeKey's privacy, data-source, address-
        protection and contact-policy sections locked.' 2026-09-24 handoff:
        this content now lives on its own reverse PAGE (not a footer) --
        extracts everything from the reverse page's own title marker
        onward and asserts it is byte-for-byte identical no matter which
        template is selected, exactly like the old footer check."""

        def _reverse_page(html_out: str) -> str:
            marker = "About this letter and your information"
            idx = html_out.index(marker)
            return html_out[idx:]

        pages = set()
        for key in letter_content.TEMPLATE_REGISTRY:
            html_out = letter_content.render_letter(
                _settings(template_key=key), lead_reference="PLANIT-001",
                address="1 Test St", summary="Fell one oak", council="Leeds",
            )
            pages.add(_reverse_page(html_out))
        self.assertEqual(len(pages), 1, "the locked reverse page must not vary by template")

    def test_reverse_page_carries_no_contractor_advertisement(self):
        """2026-09-24 handoff: 'no reverse-side advertisement.' The reverse
        page must contain the contractor's phone/contact-panel content
        nowhere -- that lives on the front page only. The single exception
        is the 'Who receives it?' data-recipient disclosure, which legitimately
        names the contractor once as a factual GDPR Art.13(1)(e) recipient
        statement ('We have not given <business_name> your name or postal
        address...') -- that is a privacy disclosure, not advertisement, and
        is asserted separately below rather than excluded."""

        def _reverse_page(html_out: str) -> str:
            return html_out[html_out.index("About this letter and your information"):]

        html_out = letter_content.render_letter(
            _settings(business_name="Apex Tree Care", phone="0113 555 0199"),
            lead_reference="PLANIT-001", address="1 Test St", summary="Fell one oak", council="Leeds",
        )
        reverse = _reverse_page(html_out)
        self.assertNotIn("0113 555 0199", reverse)
        self.assertNotIn("Speak Directly To Your Tree Surgeon", reverse)
        # The business name appears exactly once on the reverse page: inside
        # the factual "Who receives it?" recipient-disclosure sentence.
        self.assertEqual(reverse.count("Apex Tree Care"), 1)
        self.assertIn("We have not given Apex Tree Care your name or postal address", reverse)


class TestNoAutoAddedClaimWords(unittest.TestCase):
    """Section: 'Do not add automatic "trusted", "vetted", "qualified",
    "insured", "free" or availability claims.' This is a guard on
    TreeKey's OWN copy (the template registry, and the letter shell with
    every optional contractor field left blank) -- not a filter on what a
    contractor types into their own insurance/qualifications notes, which
    remain their own verbatim factual claim per this file's long-standing
    'never invented' rule."""

    def setUp(self):
        os.environ[letter_content.PRIVACY_CONTACT_EMAIL_ENV] = "privacy@treekey.co.uk"

    def test_no_banned_word_appears_in_any_template_definition(self):
        for key, template in letter_content.TEMPLATE_REGISTRY.items():
            haystack = " ".join([template.opening_line, template.quote_request_line, template.sign_off_word]).lower()
            for banned in letter_content.BANNED_AUTO_CLAIM_WORDS:
                self.assertNotIn(banned, haystack, f"template {key!r} must not contain {banned!r}")

    def test_no_banned_word_appears_in_a_fully_blank_optional_fields_render(self):
        """Every optional field left blank -- so anything left in the
        output is TreeKey's own fixed copy, not contractor content -- for
        all three templates."""
        for key in letter_content.TEMPLATE_REGISTRY:
            settings = _settings(
                template_key=key, service_area_note="", insurance_note="", qualifications_note="",
                business_intro="", services_note="", contact_email="",
            )
            html_out = letter_content.render_letter(
                settings, lead_reference="PLANIT-001", address="1 Test St",
                summary="Fell one oak", council="Leeds",
            ).lower()
            for banned in letter_content.BANNED_AUTO_CLAIM_WORDS:
                self.assertNotIn(banned, html_out, f"template {key!r}'s blank-field render must not contain {banned!r}")


class TestLengthLimitsAndContentBudget(unittest.TestCase):
    def test_business_intro_over_the_limit_is_rejected(self):
        problems = _settings(business_intro="x" * (letter_content.MAX_BUSINESS_INTRO_LEN + 1)).validate()
        self.assertTrue(any("business_intro" in p and "characters or fewer" in p for p in problems))

    def test_business_intro_at_the_limit_is_accepted(self):
        problems = _settings(business_intro="x" * letter_content.MAX_BUSINESS_INTRO_LEN).validate()
        self.assertEqual(problems, [])

    def test_services_note_over_the_limit_is_rejected(self):
        problems = _settings(services_note="x" * (letter_content.MAX_SERVICES_NOTE_LEN + 1)).validate()
        self.assertTrue(any("services_note" in p for p in problems))

    def test_combined_content_budget_is_enforced_even_when_no_single_field_exceeds_its_own_limit(self):
        """Every field individually under its own cap, but the SUM well
        over the combined budget -- must still be rejected (section:
        'content cannot overlap, disappear or spill into extra pages
        unnoticed')."""
        settings = _settings(
            business_intro="x" * letter_content.MAX_BUSINESS_INTRO_LEN,
            services_note="x" * letter_content.MAX_SERVICES_NOTE_LEN,
            insurance_note="x" * letter_content.MAX_INSURANCE_LEN,
            qualifications_note="x" * letter_content.MAX_QUALIFICATIONS_LEN,
            service_area_note="x" * letter_content.MAX_SERVICE_AREA_LEN,
        )
        problems = settings.validate()
        self.assertTrue(any("combined length" in p for p in problems))

    def test_contact_email_without_at_sign_is_rejected(self):
        problems = _settings(contact_email="not-an-email").validate()
        self.assertTrue(any("contact_email" in p for p in problems))

    def test_blank_contact_email_is_fine(self):
        self.assertEqual(_settings(contact_email="").validate(), [])


class TestRejectsMarkupInjection(unittest.TestCase):
    """Section: 'Escape user input and reject arbitrary HTML or scripts.'
    validate() actively REJECTS raw markup (rather than only relying on
    escaping at render time) so a contractor gets a clear error instead of
    their attempt being silently neutralised."""

    def test_script_tag_in_business_intro_is_rejected(self):
        problems = _settings(business_intro="Hello <script>alert(1)</script>").validate()
        self.assertTrue(any("business_intro" in p and "HTML or script" in p for p in problems))

    def test_img_onerror_in_business_name_is_rejected(self):
        problems = _settings(business_name='<img src=x onerror=alert(1)>').validate()
        self.assertTrue(any("business_name" in p and "HTML or script" in p for p in problems))

    def test_markup_in_services_note_is_rejected(self):
        problems = _settings(services_note="Pruning <b>and</b> felling").validate()
        self.assertTrue(any("services_note" in p for p in problems))

    def test_upsert_raises_rather_than_persisting_injected_markup(self):
        cur = FakeCursor()
        with self.assertRaises(ValueError):
            letter_content.upsert_contractor_settings(cur, _settings(business_intro="<script>evil()</script>"))
        self.assertEqual(cur.executed, [], "a rejected save must never reach the database")


class TestEscapingDefenseInDepth(unittest.TestCase):
    """render_letter() escapes every dynamic value unconditionally,
    regardless of whether it passed validate()'s stricter rejection --
    belt and braces for content that reaches it any other way (e.g. the
    lead's own address/summary/council, which never go through
    ContractorLetterSettings.validate at all)."""

    def setUp(self):
        os.environ[letter_content.PRIVACY_CONTACT_EMAIL_ENV] = "privacy@treekey.co.uk"

    def test_lead_sourced_fields_are_escaped_even_though_never_validated(self):
        """2026-09-24 handoff: render_letter's own markup now legitimately
        contains '<img' tags (the branding logo/marks) -- a bare
        assertNotIn("<img", ...) would false-positive on TreeKey's own
        branding, so this checks for the actual injected payload
        ('onerror=alert(1)') rather than the tag name alone."""
        html_out = letter_content.render_letter(
            _settings(), lead_reference="PLANIT-001",
            address='1 Test St <script>alert("addr")</script>',
            summary='Fell <img src=x onerror=alert(1)> one oak',
            council='<b>Leeds</b> Council',
        )
        self.assertNotIn("<script>", html_out)
        self.assertNotIn("<img src=x", html_out)
        self.assertNotIn("<b>Leeds</b>", html_out)
        self.assertIn("&lt;script&gt;", html_out)
        # The injected payload's '<'/'>' must be neutralised (escaped), even
        # though the harmless remaining text ("onerror=alert(1)") -- no
        # longer inside real tag syntax once escaped -- legitimately still
        # appears verbatim as inert text.
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html_out)

    def test_settings_constructed_directly_bypassing_validate_are_still_escaped_at_render(self):
        """A settings object assembled directly in Python (bypassing
        upsert_contractor_settings/validate entirely) must still come out
        escaped -- render_letter never trusts its caller."""
        settings = letter_content.ContractorLetterSettings(
            contractor_email="x@example.com", business_name='Apex & <b>Sons</b>', phone="0113 000 0000",
            business_intro='Family run <script>steal()</script> business',
        )
        html_out = letter_content.render_letter(
            settings, lead_reference="PLANIT-001", address="1 Test St", summary="Fell one oak", council="Leeds",
        )
        self.assertNotIn("<script>steal()</script>", html_out)
        self.assertNotIn("<b>Sons</b>", html_out)
        self.assertIn("&amp;", html_out)


class TestNewEditableFieldsRenderWhenPresent(unittest.TestCase):
    def setUp(self):
        os.environ[letter_content.PRIVACY_CONTACT_EMAIL_ENV] = "privacy@treekey.co.uk"

    def test_business_intro_services_note_and_contact_email_appear_when_set(self):
        html_out = letter_content.render_letter(
            _settings(business_intro="We have worked in this area for years.",
                      services_note="Tree felling, pruning and stump grinding.",
                      contact_email="office@apextreecare.example",
                      service_area_note="Leeds & surrounding"),
            lead_reference="PLANIT-001", address="1 Test St", summary="Fell one oak", council="Leeds",
        )
        self.assertIn("We have worked in this area for years.", html_out)
        self.assertIn("Tree felling, pruning and stump grinding.", html_out)
        self.assertIn("office@apextreecare.example", html_out)
        self.assertIn("Leeds &amp; surrounding", html_out)

    def test_absent_when_blank_not_a_fabricated_default(self):
        html_out = letter_content.render_letter(
            _settings(business_intro="", services_note="", contact_email="", service_area_note=""),
            lead_reference="PLANIT-001", address="1 Test St", summary="Fell one oak", council="Leeds",
        )
        self.assertNotIn("Area covered:", html_out)
        self.assertNotIn("<b>Services:</b>", html_out)


class TestFingerprintCoversNewFields(unittest.TestCase):
    def test_changing_template_key_changes_the_fingerprint(self):
        fp1 = letter_content.template_fingerprint(_settings(template_key="friendly_introduction"))
        fp2 = letter_content.template_fingerprint(_settings(template_key="short_and_direct"))
        self.assertNotEqual(fp1, fp2)

    def test_changing_business_intro_changes_the_fingerprint(self):
        fp1 = letter_content.template_fingerprint(_settings(business_intro="A"))
        fp2 = letter_content.template_fingerprint(_settings(business_intro="B"))
        self.assertNotEqual(fp1, fp2)

    def test_changing_services_note_changes_the_fingerprint(self):
        fp1 = letter_content.template_fingerprint(_settings(services_note="A"))
        fp2 = letter_content.template_fingerprint(_settings(services_note="B"))
        self.assertNotEqual(fp1, fp2)

    def test_changing_contact_email_changes_the_fingerprint(self):
        fp1 = letter_content.template_fingerprint(_settings(contact_email="a@example.com"))
        fp2 = letter_content.template_fingerprint(_settings(contact_email="b@example.com"))
        self.assertNotEqual(fp1, fp2)

    def test_editing_a_templates_own_wording_changes_the_fingerprint_for_everyone_on_it(self):
        """Section: 'Any material change to wording ... must invalidate
        reusable approval.' This must be true not only when a CONTRACTOR
        edits their own fields, but also when Nick's placeholder copy in
        TEMPLATE_REGISTRY is later replaced with final wording -- a
        contractor's existing approval must not silently carry over onto
        different wording it never actually approved. Simulated here by
        monkeypatching the registry entry's opening_line (same template_key,
        same everything else on the contractor's own settings)."""
        settings = _settings(template_key="friendly_introduction")
        fp_before = letter_content.template_fingerprint(settings)

        original = letter_content.TEMPLATE_REGISTRY["friendly_introduction"]
        edited = letter_content.LetterTemplateDefinition(
            key=original.key, label=original.label, version=original.version + 1,
            opening_line="Completely different final wording from Nick.",
            quote_request_line=original.quote_request_line, sign_off_word=original.sign_off_word,
        )
        letter_content.TEMPLATE_REGISTRY["friendly_introduction"] = edited
        try:
            fp_after = letter_content.template_fingerprint(settings)
        finally:
            letter_content.TEMPLATE_REGISTRY["friendly_introduction"] = original

        self.assertNotEqual(fp_before, fp_after)

    def test_editing_the_reverse_page_content_version_changes_the_fingerprint_for_everyone(self):
        """2026-09-24 handoff: the reverse page (privacy/supporting info) is
        TreeKey's own locked copy, identical across every contractor and
        template -- not a contractor-editable field and not part of
        TEMPLATE_REGISTRY. The same 'any material change to wording ...
        must invalidate reusable approval' requirement applies to it, via
        REVERSE_PAGE_CONTENT_VERSION being baked into template_fingerprint's
        material. Simulated here by monkeypatching that module-level
        constant, the same way the previous test monkeypatches a
        TEMPLATE_REGISTRY entry to simulate a future wording swap."""
        settings = _settings(template_key="friendly_introduction")
        fp_before = letter_content.template_fingerprint(settings)

        original_version = letter_content.REVERSE_PAGE_CONTENT_VERSION
        letter_content.REVERSE_PAGE_CONTENT_VERSION = original_version + 1
        try:
            fp_after = letter_content.template_fingerprint(settings)
        finally:
            letter_content.REVERSE_PAGE_CONTENT_VERSION = original_version

        self.assertNotEqual(fp_before, fp_after)


if __name__ == "__main__":
    unittest.main()
