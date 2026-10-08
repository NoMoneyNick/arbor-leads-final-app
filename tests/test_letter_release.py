"""
test_letter_release.py -- the 7 Oct 2026 letter release.

Covers three things only:
  1. the recipient address prints one part per line (line breaks only, text never changed);
  2. the approved reverse-page identity ("Who is responsible?") and objection ("Stop further
     marketing") wording, with the correspondence address and the letter reference handled as
     specified (address offered only when configured; reference optional);
  3. the retention notice is deliberately UNCHANGED and still pending Intelliprint's written
     answers (a guard so no assumption is slipped in silently).

Run with:
    python -m unittest tests.test_letter_release -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import letter_content


def _settings(**overrides):
    base = dict(contractor_email="contractor@example.com", business_name="Apex Tree Care",
                phone="0113 000 0000")
    base.update(overrides)
    return letter_content.ContractorLetterSettings(**base)


ADDRESS = "Nicholas Secular / TreeKey, Unit 173384, PO Box 7169, Poole, BH15 9EL"


class _EnvCase(unittest.TestCase):
    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in (letter_content.PRIVACY_CONTACT_EMAIL_ENV,
                                                      letter_content.CORRESPONDENCE_ADDRESS_ENV)}
        os.environ[letter_content.PRIVACY_CONTACT_EMAIL_ENV] = "nick@treekey.uk"
        os.environ.pop(letter_content.CORRESPONDENCE_ADDRESS_ENV, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _render(self, address="1 A Road, Town, AB1 2CD", ref="REF-1"):
        return letter_content.render_letter(_settings(), lead_reference=ref, address=address,
                                            summary="s", council="c")

    @staticmethod
    def _section(html, heading):
        start = html.index('<div class="notice-heading">%s</div>' % heading)
        end = html.index("</div>\n  </div>", start) if "</div>\n  </div>" in html[start:] else html.index("</p>", start)
        return " ".join(html[start:html.index("</p>", start)].split())


class TestRecipientAddressLayout(_EnvCase):
    def _block(self, address):
        html = self._render(address=address)
        start = html.index('<div class="recipient-address">') + len('<div class="recipient-address">')
        return html[start:html.index("</div>", start)]

    def test_commas_become_line_breaks_text_unchanged(self):
        lines = letter_content.format_address_lines("124 Coulsdon Road, Old Coulsdon, CR5 2LE")
        self.assertEqual(lines, ["124 Coulsdon Road", "Old Coulsdon", "CR5 2LE"])
        self.assertEqual(", ".join(lines), "124 Coulsdon Road, Old Coulsdon, CR5 2LE")

    def test_existing_line_breaks_are_kept_exactly_and_commas_inside_are_not_split(self):
        lines = letter_content.format_address_lines("Flat 1, Oak House\r\n12 High Street\nTown\nAB1 2CD")
        self.assertEqual(lines, ["Flat 1, Oak House", "12 High Street", "Town", "AB1 2CD"])

    def test_nothing_is_guessed_or_split_without_a_comma(self):
        self.assertEqual(letter_content.format_address_lines("12 Meadow Lane Old Coulsdon CR5 2LE"),
                         ["12 Meadow Lane Old Coulsdon CR5 2LE"])
        self.assertEqual(letter_content.format_address_lines("cr5 2le"), ["cr5 2le"])

    def test_blank_parts_and_surrounding_spaces_are_dropped_only(self):
        self.assertEqual(letter_content.format_address_lines("  1 A Road ,, Town ,  AB1 2CD , "),
                         ["1 A Road", "Town", "AB1 2CD"])
        self.assertEqual(letter_content.format_address_lines(""), [])
        self.assertEqual(letter_content.format_address_lines(None), [])

    def test_rendered_block_has_one_part_per_line_in_order(self):
        self.assertEqual(self._block("124 Coulsdon Road, Old Coulsdon, CR5 2LE").split("\n"),
                         ["124 Coulsdon Road", "Old Coulsdon", "CR5 2LE"])

    def test_html_in_an_address_is_still_escaped(self):
        block = self._block("1 <b>Road</b>, A & B, AB1 2CD")
        self.assertNotIn("<b>", block)
        self.assertIn("&lt;b&gt;Road&lt;/b&gt;", block)
        self.assertIn("A &amp; B", block)

    def test_block_keeps_the_pre_line_style_and_clear_zone_order(self):
        html = self._render()
        self.assertIn(".recipient-address { font-size: 12.5px; font-weight: bold; white-space: pre-line; }", html)
        self.assertLess(html.index('<div class="address-clear-zone">'), html.index('class="recipient-address"'))
        self.assertLess(html.index('class="recipient-address"'), html.index('class="brand-header"'))


class TestWhoIsResponsible(_EnvCase):
    def test_names_the_sole_trader_and_never_vector_data_labs(self):
        os.environ[letter_content.CORRESPONDENCE_ADDRESS_ENV] = ADDRESS
        html = self._render()
        self.assertNotIn("Vector Data Labs", html)
        sec = self._section(html, "Who is responsible?")
        self.assertIn("TreeKey is the trading name of Nicholas Michael Secular, a sole trader, who is responsible "
                      "for your information.", sec)
        self.assertIn("You can write to " + ADDRESS + " or email <b>nick@treekey.uk</b>.", sec)

    def test_without_an_address_only_the_email_route_is_offered_and_nothing_is_invented(self):
        sec = self._section(self._render(), "Who is responsible?")
        self.assertIn("You can email <b>nick@treekey.uk</b>.", sec)
        self.assertNotIn("write to", sec)
        self.assertNotIn("PO Box", sec)

    def test_address_is_html_escaped(self):
        os.environ[letter_content.CORRESPONDENCE_ADDRESS_ENV] = "A & B <i>Ltd</i>, 1 Road"
        sec = self._section(self._render(), "Who is responsible?")
        self.assertIn("A &amp; B &lt;i&gt;Ltd&lt;/i&gt;, 1 Road", sec)
        self.assertNotIn("<i>", sec)


class TestStopFurtherMarketing(_EnvCase):
    def test_approved_objection_wording_with_address(self):
        os.environ[letter_content.CORRESPONDENCE_ADDRESS_ENV] = ADDRESS
        sec = self._section(self._render(ref="PLANIT-77"), "Stop further marketing")
        self.assertIn("You have the right to object, at any time and without giving a reason, to TreeKey using "
                      "your information for marketing.", sec)
        self.assertIn("Email <b>nick@treekey.uk</b> or write to the address above, and include the letter "
                      "reference <b>PLANIT-77</b> if you have it (it is not required).", sec)
        self.assertIn("We will stop using your information for TreeKey marketing and record your request. "
                      "If a letter is already in production, it may still reach you.", sec)

    def test_without_an_address_no_dangling_reference_to_one(self):
        sec = self._section(self._render(), "Stop further marketing")
        self.assertIn("Email <b>nick@treekey.uk</b>, and include", sec)
        self.assertNotIn("address above", sec)

    def test_reference_is_optional_and_old_promises_are_gone(self):
        sec = self._section(self._render(), "Stop further marketing")
        self.assertIn("if you have it (it is not required)", sec)
        for gone in ("any future TreeKey marketing", "will not send another marketing letter",
                     "at this address", "cannot be recalled", "can't be recalled"):
            self.assertNotIn(gone, sec)


class TestRetentionNoticeUsesTheConfirmedFacts(_EnvCase):
    """Intelliprint's written reply (8 Oct 2026, as reported by Nick): the PDF is kept 90 days from job
    confirmation; its separate recipient and letter records have no automatic deletion period and can be
    deleted on request, subject to legal requirements. TreeKey's own deletion is stated separately and no
    provider deletion guarantee is invented."""

    def _sec(self):
        return " ".join(self._section(self._render(), "How long is it kept?").split())

    def test_own_deletion_is_stated_separately_and_tied_to_handover_to_royal_mail(self):
        sec = self._sec()
        self.assertIn("About 72 hours after we record that this letter has been handed to Royal Mail, we delete "
                      "your name, address and this letter's content from our own live systems.", sec)

    def test_provider_retention_is_exactly_the_confirmed_facts(self):
        sec = self._sec()
        self.assertIn("Our printing provider keeps a PDF of the letter for 90 days from when the print job is "
                      "confirmed, and its separate recipient and letter records have no automatic deletion period; "
                      "you can ask us to request their deletion, subject to legal requirements.", sec)

    def test_no_provider_deletion_guarantee_and_no_unconfirmed_claims(self):
        sec = self._sec().lower()
        for invented in ("we guarantee", "guaranteed", "will be deleted by", "intelliprint", "data processing agreement",
                         "subprocessor", "sub-processor", "uk and eu", "within the uk", "delivered", "has been dispatched"):
            self.assertNotIn(invented, sec)

    def test_minimal_record_sentence_is_unchanged(self):
        self.assertIn("A minimal record of the transaction (needed for accounting and to handle any complaint) is "
                      "kept for longer; we have not yet set a fixed expiry for that minimal record.", self._sec())

    def test_letter_reverse_does_not_name_the_provider_or_claim_a_dpa(self):
        html = self._render()
        reverse = html[html.index("About this letter and your information"):]
        self.assertNotIn("Intelliprint", reverse)
        self.assertNotIn("processing agreement", reverse)


if __name__ == "__main__":
    unittest.main()
