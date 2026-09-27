"""
test_providers.py -- letter_providers/*, including the registry's fallback
rules (section 6's most safety-critical logic). Run with:
    python -m unittest tests.test_providers -v

standalone_mailer/mailer.py itself was smoke-tested separately, live, in an
isolated sandbox earlier this session (dry-run send, idempotent replay,
and same-key-different-content rejection all verified by direct
execution) -- see docs/handoff.md for that transcript. These tests cover
the NEW adapter interface built on the same pattern.
"""
import email.message
import io
import json
import os
import sys
import unittest
import urllib.error
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import fulfilment
import suppression

# 2026-09-22 handoff: suppression.is_suppressed (called from registry.py's
# attempt_send, which this file drives extensively) now requires
# SUPPRESSION_HASH_KEY to be set (fails loud, not safe-and-silent -- see
# suppression.py::_hash_key's own docstring). Not a real secret, just a
# fixed non-empty string so hashing is deterministic for these tests, none
# of which are testing suppression's own hashing behaviour (see
# tests/test_suppression.py for that).
os.environ.setdefault(suppression.SUPPRESSION_HASH_KEY_ENV, "unit-test-suppression-hash-key-not-a-real-secret")
from letter_providers.base import LetterRequest, ProviderResult, fingerprint_content, OUTCOME_ACCEPTED, OUTCOME_DISPATCHED, OUTCOME_REJECTED, OUTCOME_UNKNOWN
from letter_providers.fake_provider import FakeLetterProvider
from letter_providers.stannp_provider import StannpProvider
from letter_providers.intelliprint_provider import IntelliprintProvider
from letter_providers.postworks_provider import PostworksProvider
from letter_providers.registry import ProviderRegistry, ProviderSlot, attempt_send


class FakeCursor:
    def __init__(self, fetchone_results=None):
        self.executed = []
        self._fetchone_results = list(fetchone_results or [])

    def execute(self, sql, params=None):
        self.executed.append((sql.strip(), params))

    def fetchone(self):
        return self._fetchone_results.pop(0) if self._fetchone_results else None


def _request(key="idem-1"):
    return LetterRequest(
        idempotency_key=key, lead_reference="PLANIT-001",
        address_lines={"line1": "1 Test St", "city": "Leeds", "postcode": "LS1 1AA", "country": "GB"},
        applicant_name="J Bloggs", content_html="<html>hi</html>",
        content_fingerprint=fingerprint_content("<html>hi</html>"),
    )


class TestFakeProvider(unittest.TestCase):
    def test_accepts_by_default(self):
        provider = FakeLetterProvider()
        result = provider.send(_request())
        self.assertEqual(result.outcome, OUTCOME_ACCEPTED)
        self.assertTrue(result.provider_reference.startswith("FAKE-"))

    def test_duplicate_idempotency_key_returns_cached_result_not_a_new_send(self):
        provider = FakeLetterProvider()
        first = provider.send(_request(key="dup-key"))
        second = provider.send(_request(key="dup-key"))
        self.assertEqual(first.provider_reference, second.provider_reference)

    def test_forced_rejection(self):
        provider = FakeLetterProvider(force_outcome="rejected")
        self.assertEqual(provider.send(_request()).outcome, OUTCOME_REJECTED)

    def test_forced_unknown(self):
        provider = FakeLetterProvider(force_outcome="unknown")
        self.assertEqual(provider.send(_request()).outcome, OUTCOME_UNKNOWN)

    def test_reject_specific_postcodes(self):
        provider = FakeLetterProvider(reject_postcodes={"LS1 1AA"})
        self.assertEqual(provider.send(_request()).outcome, OUTCOME_REJECTED)


class TestUnimplementedProvidersAreHonest(unittest.TestCase):
    def test_intelliprint_reports_not_configured_without_credentials(self):
        os.environ.pop("INTELLIPRINT_API_KEY", None)
        self.assertFalse(IntelliprintProvider().is_configured())

    def test_postworks_reports_not_configured(self):
        self.assertFalse(PostworksProvider().is_configured())

    def test_postworks_raises_rather_than_pretending_to_work(self):
        with self.assertRaises(NotImplementedError):
            PostworksProvider().send(_request())

    def test_stannp_reports_not_configured_without_credentials(self):
        os.environ.pop("STANNP_API_KEY", None)
        os.environ.pop("STANNP_TEMPLATE_ID", None)
        self.assertFalse(StannpProvider().is_configured())


def _fake_http_response(body: dict, status: int = 200):
    """A context-manager-compatible stand-in for the object
    build_opener().open(...) returns, matching how
    intelliprint_provider.py reads it (`with ... as response:
    response.read()`)."""
    class _Resp:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def read(self, n=None):
            return json.dumps(body).encode("utf-8")
    return _Resp()


def _fake_http_error(status: int, reason: str, body_bytes: bytes, headers: dict = None) -> urllib.error.HTTPError:
    """Builds a real urllib.error.HTTPError (not a mock) with a readable
    body and a header collection that behaves like the one
    intelliprint_provider.py actually receives (http.client.HTTPMessage
    supports the same .keys()/.get() interface as email.message.Message,
    which this uses as a lightweight stand-in)."""
    msg = email.message.Message()
    for k, v in (headers or {}).items():
        msg[k] = v
    return urllib.error.HTTPError(
        url="https://api.intelliprint.net/v1/prints",
        code=status, msg=reason, hdrs=msg, fp=io.BytesIO(body_bytes),
    )


class TestIntelliprintProviderConfiguration(unittest.TestCase):
    def tearDown(self):
        os.environ.pop("INTELLIPRINT_API_KEY", None)
        os.environ.pop("INTELLIPRINT_TEST_MODE", None)

    def test_not_configured_without_api_key(self):
        os.environ.pop("INTELLIPRINT_API_KEY", None)
        self.assertFalse(IntelliprintProvider().is_configured())

    def test_configured_with_api_key(self):
        os.environ["INTELLIPRINT_API_KEY"] = "test-key-123"
        self.assertTrue(IntelliprintProvider().is_configured())

    def test_test_mode_defaults_true(self):
        os.environ["INTELLIPRINT_API_KEY"] = "test-key-123"
        os.environ.pop("INTELLIPRINT_TEST_MODE", None)
        self.assertTrue(IntelliprintProvider().test_mode)

    def test_test_mode_can_be_explicitly_disabled(self):
        os.environ["INTELLIPRINT_API_KEY"] = "test-key-123"
        os.environ["INTELLIPRINT_TEST_MODE"] = "false"
        self.assertFalse(IntelliprintProvider().test_mode)

    def test_send_without_api_key_is_unknown_not_a_crash(self):
        os.environ.pop("INTELLIPRINT_API_KEY", None)
        result = IntelliprintProvider().send(_request())
        self.assertEqual(result.outcome, OUTCOME_UNKNOWN)


class TestIntelliprintProviderSendMapping(unittest.TestCase):
    """Mocks the HTTP layer only -- exercises the real request-building and
    response-mapping code in intelliprint_provider.py against the field
    names/status values verified against Intelliprint's current live docs
    (see that module's own docstring for citations)."""

    def setUp(self):
        os.environ["INTELLIPRINT_API_KEY"] = "test-key-123"

    def tearDown(self):
        os.environ.pop("INTELLIPRINT_API_KEY", None)
        os.environ.pop("INTELLIPRINT_TEST_MODE", None)

    def _send_with_response(self, body):
        provider = IntelliprintProvider()
        # 2026-09-26, eighth fix: send() now renders the letter to a real
        # PDF (via Playwright/Chromium) before submitting it. Mocked here
        # to fixed bytes -- this test class exercises request-building and
        # response-mapping, not PDF rendering fidelity (that is covered
        # empirically by tests/letter_pagination_check/run_pagination_check.py,
        # which already uses the real engine; invoking real Chromium in
        # every one of these tests would make the main suite slow and add
        # a hard Playwright/Chromium dependency to `unittest discover`,
        # which this codebase deliberately avoids elsewhere -- see that
        # script's own docstring).
        with patch("letter_providers.intelliprint_provider.build_opener") as mock_build_opener, \
             patch("letter_providers.intelliprint_provider._render_html_to_pdf_bytes",
                   return_value=b"%PDF-1.4 fake pdf bytes for testing") as mock_render:
            mock_build_opener.return_value.open.return_value = _fake_http_response(body)
            result = provider.send(_request())
        request_sent = mock_build_opener.return_value.open.call_args[0][0]
        self.assertEqual(mock_render.call_count, 1)  # rendered exactly once, not skipped or repeated
        return result, request_sent

    def test_draft_status_maps_to_accepted(self):
        result, _ = self._send_with_response({"id": "print_abc", "status": "draft",
                                                "letters": [{"id": "letter_1", "status": "draft"}]})
        self.assertEqual(result.outcome, OUTCOME_ACCEPTED)
        self.assertEqual(result.provider_reference, "print_abc")

    def test_printing_status_maps_to_accepted(self):
        result, _ = self._send_with_response({"id": "print_abc",
                                                "letters": [{"id": "letter_1", "status": "printing"}]})
        self.assertEqual(result.outcome, OUTCOME_ACCEPTED)

    def test_sent_status_maps_to_dispatched(self):
        result, _ = self._send_with_response({"id": "print_abc",
                                                "letters": [{"id": "letter_1", "status": "sent"}]})
        self.assertEqual(result.outcome, OUTCOME_DISPATCHED)

    def test_invalid_address_maps_to_rejected(self):
        result, _ = self._send_with_response({"id": "print_abc",
                                                "letters": [{"id": "letter_1", "status": "invalid_address"}]})
        self.assertEqual(result.outcome, OUTCOME_REJECTED)

    def test_cancelled_maps_to_rejected(self):
        result, _ = self._send_with_response({"id": "print_abc",
                                                "letters": [{"id": "letter_1", "status": "cancelled"}]})
        self.assertEqual(result.outcome, OUTCOME_REJECTED)

    def test_unrecognised_status_maps_to_unknown_not_guessed(self):
        result, _ = self._send_with_response({"id": "print_abc",
                                                "letters": [{"id": "letter_1", "status": "some_new_status_we_have_never_seen"}]})
        self.assertEqual(result.outcome, OUTCOME_UNKNOWN)

    def test_error_body_maps_to_rejected(self):
        result, _ = self._send_with_response({"error": {"type": "authentication_error", "message": "bad key"}})
        self.assertEqual(result.outcome, OUTCOME_REJECTED)
        self.assertIn("authentication_error", result.message)

    def test_non_dict_body_maps_to_unknown(self):
        provider = IntelliprintProvider()
        with patch("letter_providers.intelliprint_provider.build_opener") as mock_build_opener:
            mock_build_opener.return_value.open.return_value = _fake_http_response(["not", "a", "dict"])
            # json.dumps(list) still parses back to a list -- exercise the
            # isinstance(data, dict) guard directly.
            result = provider.send(_request())
        self.assertEqual(result.outcome, OUTCOME_UNKNOWN)

    def test_network_exception_maps_to_unknown_never_rejected(self):
        provider = IntelliprintProvider()
        with patch("letter_providers.intelliprint_provider.build_opener") as mock_build_opener:
            mock_build_opener.return_value.open.side_effect = TimeoutError("simulated timeout")
            result = provider.send(_request())
        self.assertEqual(result.outcome, OUTCOME_UNKNOWN)

    def test_cost_pence_conversion(self):
        result, _ = self._send_with_response({
            "id": "print_abc", "status": "draft",
            "cost": {"amount": 100000000, "tax": 20000000, "after_tax": 120000000, "currency": "GBP"},
        })
        self.assertEqual(result.cost_pence, 120)

    def test_request_uses_bearer_auth_header(self):
        _, request_sent = self._send_with_response({"id": "print_abc", "status": "draft"})
        self.assertEqual(request_sent.get_header("Authorization"), "Bearer test-key-123")

    def test_request_sends_an_honest_non_browser_user_agent(self):
        # 2026-09-26, third fix: no User-Agent at all left urllib sending
        # its own generic default, which Cloudflare could reject outright
        # (browser_signature_banned) before Intelliprint's own app code
        # ever saw the request. Must be an honest app identifier -- never
        # a spoofed/impersonated browser string.
        _, request_sent = self._send_with_response({"id": "print_abc", "status": "draft"})
        user_agent = request_sent.get_header("User-agent")
        self.assertEqual(user_agent, "TreeKey/1.0 (+https://treekey.co.uk)")
        for browser_token in ("Mozilla", "Chrome", "Safari", "AppleWebKit", "Gecko"):
            self.assertNotIn(browser_token, user_agent)

    def test_request_defaults_to_testmode_true(self):
        _, request_sent = self._send_with_response({"id": "print_abc", "status": "draft"})
        body = request_sent.data.decode()
        self.assertIn("testmode=true", body)

    def test_request_sets_double_sided_yes_so_reverse_page_prints_on_the_back(self):
        # 2026-09-26, fourth fix: previously unset -- Intelliprint's own
        # documented default (double_sided="no") would print this letter's
        # two pages as two separate one-sided sheets, not one sheet
        # printed front and back as the two-page design intends.
        _, request_sent = self._send_with_response({"id": "print_abc", "status": "draft"})
        body = request_sent.data.decode()
        self.assertIn("printing%5Bdouble_sided%5D=yes", body)

    def test_request_never_sends_add_address_sheet(self):
        # 2026-09-26, seventh fix: a real submission returned HTTP 400
        # parameter_unknown for this exact field. Re-checked
        # reference/prints/create's REQUEST schema directly: add_address_sheet
        # is documented ONLY in the RESPONSE ("whether the address is
        # printed on a separate page... or the same page as your letter") --
        # it is Intelliprint's own report of what it did, not a
        # caller-settable input, and no other documented field controls
        # this either. Must never be sent again.
        _, request_sent = self._send_with_response({"id": "print_abc", "status": "draft"})
        body = request_sent.data.decode()
        self.assertNotIn("add_address_sheet", body)

    def test_request_only_uses_documented_request_schema_fields(self):
        # 2026-09-26, seventh fix: the add_address_sheet 400 happened
        # because a field was added to the payload that "sounded right" but
        # was never actually checked against Intelliprint's REQUEST schema
        # (it turned out to be response-only). This test guards against
        # that class of mistake recurring for ANY field, not just this one:
        # every top-level key this adapter sends must be one of the fields
        # reference/prints/create documents as REQUEST parameters (checked
        # 2026-09-26 via Intelliprint's current live docs -- see this
        # module's own docstring, seventh fix, for the full list and its
        # source). A key like "printing[double_sided]" is checked by its
        # top-level name ("printing") since Intelliprint's own docs nest
        # sub-fields that way.
        DOCUMENTED_REQUEST_TOP_LEVEL_FIELDS = {
            "type", "testmode", "confirmed", "content", "template", "file",
            "reference", "mailing_list", "recipients", "splitting", "printing",
            "postage", "background", "confidential", "extra_documents",
            "remove_letters", "nudge", "confirmation_email", "address_window",
            "insert", "metadata",
        }
        _, request_sent = self._send_with_response({"id": "print_abc", "status": "draft"})
        body = request_sent.data.decode()
        sent_top_level_names = set()
        for pair in body.split("&"):
            key = pair.split("=", 1)[0]
            top_level = key.split("%5B", 1)[0].split("[", 1)[0]  # strip [nested] suffix, encoded or not
            sent_top_level_names.add(top_level)
        undocumented = sent_top_level_names - DOCUMENTED_REQUEST_TOP_LEVEL_FIELDS
        self.assertEqual(undocumented, set(),
                          f"Sending undocumented request field(s): {undocumented} -- "
                          f"verify against Intelliprint's current live docs before adding, don't guess.")

    def test_pdf_preview_url_surfaces_in_result_message_when_present(self):
        # Intelliprint's own documented response shape (reference/prints/
        # retrieve): each letter carries a signed `pdf` preview URL. This
        # must reach ProviderResult.message so it's visible in
        # scripts/intelliprint_test_send.py's own printed output --
        # the only reliable way to inspect what was actually rendered,
        # rather than assuming a successful submission means correct
        # rendering.
        result, _ = self._send_with_response({
            "id": "print_abc", "status": "draft",
            "letters": [{"id": "letter_1", "status": "draft",
                         "pdf": "https://api.intelliprint.net/files/signed/letter_1.pdf?sig=abc"}],
        })
        self.assertIn("https://api.intelliprint.net/files/signed/letter_1.pdf?sig=abc", result.message)

    def test_missing_pdf_preview_url_does_not_crash_and_says_so(self):
        result, _ = self._send_with_response({"id": "print_abc", "status": "draft"})
        self.assertNotIn("None", result.message)
        self.assertIn("no PDF preview URL", result.message)

    def test_pages_sheets_surfaced_and_flagged_as_duplex_evidence_when_sheets_less_than_pages(self):
        # 2026-09-26, fifth fix: Nick's correction -- "two PDF pages alone
        # do not establish front-and-back printing." pages=2/sheets=1 IS
        # that evidence (per Intelliprint's own docs: sheets < pages means
        # double-sided printing was actually used).
        result, _ = self._send_with_response({"id": "print_abc", "status": "draft", "pages": 2, "sheets": 1})
        self.assertIn("pages=2", result.message)
        self.assertIn("sheets=1", result.message)
        self.assertIn("consistent with duplex", result.message)

    def test_pages_sheets_flagged_as_not_duplexed_when_equal_and_over_one(self):
        # The opposite, equally important case: if double_sided=yes was
        # sent but Intelliprint still printed two separate one-sided
        # sheets, sheets would equal pages (both 2) -- must be called out,
        # not silently treated as fine.
        result, _ = self._send_with_response({"id": "print_abc", "status": "draft", "pages": 2, "sheets": 2})
        self.assertIn("does NOT look duplexed", result.message)

    def test_missing_pages_sheets_does_not_crash_and_says_so(self):
        result, _ = self._send_with_response({"id": "print_abc", "status": "draft"})
        self.assertIn("cannot confirm duplex", result.message)

    def test_response_add_address_sheet_is_surfaced_when_present(self):
        # 2026-09-26, seventh fix: add_address_sheet turned out to be
        # response-only (see send()'s payload -- sending it as a request
        # field caused a real 400). Surfaced here instead, since it's
        # Intelliprint's own statement of whether it separated the address
        # onto its own sheet -- exactly what Nick's original report needs
        # confirmed from the provider, not assumed.
        result, _ = self._send_with_response({"id": "print_abc", "status": "draft", "add_address_sheet": False})
        self.assertIn("add_address_sheet=False", result.message)

    def test_testmode_false_is_actually_sent_when_explicitly_configured(self):
        os.environ["INTELLIPRINT_TEST_MODE"] = "false"
        _, request_sent = self._send_with_response({"id": "print_abc", "status": "draft"})
        body = request_sent.data.decode()
        self.assertIn("testmode=false", body)

    def test_request_never_sends_recipients(self):
        # 2026-09-26, eighth fix: superseded test_address_line_and_city_are_combined
        # above (which asserted the OLD recipients[0][address][line] field
        # this adapter no longer sends at all). Per
        # reference/prints/create's documented base64 file-upload route:
        # "you do not need to provide recipients as Intelliprint will
        # automatically extract the addresses from the file" -- our PDF
        # already carries the address in letter_content.py's
        # .address-clear-zone. Omitted deliberately, not by oversight; see
        # this module's docstring, eighth fix, for the open question this
        # leaves for real (non-test) sends.
        _, request_sent = self._send_with_response({"id": "print_abc", "status": "draft"})
        body = request_sent.data.decode()
        self.assertNotIn("recipients", body)

    def test_request_submits_rendered_pdf_as_base64_file_content(self):
        # 2026-09-26, eighth fix: content (raw HTML) is no longer sent --
        # replaced by file[content] (base64-encoded rendered PDF) and
        # file[name], per reference/prints/create's documented base64
        # upload shape, since Intelliprint's HTML-content strategy was
        # confirmed (Nick's real submission) to add its own extra
        # address/barcode page that the file strategy documents avoiding.
        _, request_sent = self._send_with_response({"id": "print_abc", "status": "draft"})
        body = request_sent.data.decode()
        self.assertNotIn("content=", body)  # old field, must be gone
        self.assertIn("file%5Bname%5D=treekey_idem-1.pdf", body)
        # The fake PDF bytes _send_with_response mocks in, base64-encoded
        # and urlencoded, must appear as file[content]'s value.
        import base64
        expected_b64 = base64.b64encode(b"%PDF-1.4 fake pdf bytes for testing").decode("ascii")
        from urllib.parse import quote
        self.assertIn(f"file%5Bcontent%5D={quote(expected_b64, safe='')}", body)

    def test_render_failure_is_unknown_and_never_touches_the_network(self):
        # A local rendering failure (e.g. Playwright/Chromium missing or
        # broken) means nothing was sent to Intelliprint at all -- must
        # never be misreported as accepted/rejected, and must not attempt
        # a network call with no PDF to send.
        provider = IntelliprintProvider()
        with patch("letter_providers.intelliprint_provider.build_opener") as mock_build_opener, \
             patch("letter_providers.intelliprint_provider._render_html_to_pdf_bytes",
                   side_effect=RuntimeError("playwright is not installed. ...")):
            result = provider.send(_request())
        self.assertEqual(result.outcome, OUTCOME_UNKNOWN)
        self.assertIn("Could not render the letter to PDF", result.message)
        mock_build_opener.assert_not_called()


class TestIntelliprintProviderHTTPErrorClassification(unittest.TestCase):
    """2026-09-26, second fix: opener.open() raises urllib.error.HTTPError
    on any non-2xx response BEFORE the old code got a chance to read the
    body -- Nick's real "HTTP Error 403: Forbidden" test run hit exactly
    this, with the actual diagnostic body/headers discarded. These tests
    cover the replacement behaviour: a body that matches Intelliprint's own
    documented {"error": {...}} shape is a confirmed rejection; anything
    else (e.g. an HTML page from a security/WAF layer) stays UNKNOWN, and
    the API key / Authorization header value is never present in either
    outcome's message."""

    def setUp(self):
        os.environ["INTELLIPRINT_API_KEY"] = "sk_super_secret_key_value"

    def tearDown(self):
        os.environ.pop("INTELLIPRINT_API_KEY", None)
        os.environ.pop("INTELLIPRINT_TEST_MODE", None)

    def _send_with_http_error(self, exc):
        provider = IntelliprintProvider()
        with patch("letter_providers.intelliprint_provider.build_opener") as mock_build_opener:
            mock_build_opener.return_value.open.side_effect = exc
            return provider.send(_request())

    def test_403_with_documented_error_body_is_rejected_with_evidence(self):
        body = json.dumps({"error": {
            "type": "authentication_error", "code": "forbidden",
            "message": "The API key provided was not authorised to access the requested resource.",
            "param": None,
        }}).encode("utf-8")
        exc = _fake_http_error(403, "Forbidden", body, headers={
            "Content-Type": "application/json", "X-Request-Id": "req_abc123",
            "Authorization": "Bearer sk_super_secret_key_value",
        })
        result = self._send_with_http_error(exc)
        self.assertEqual(result.outcome, OUTCOME_REJECTED)
        self.assertIn("forbidden", result.message)
        self.assertIn("not authorised", result.message)
        self.assertIn("req_abc123", result.message)

    def test_403_with_non_json_body_stays_unknown_not_guessed_rejected(self):
        # A security/WAF layer in front of the API would typically return
        # HTML or plain text, not Intelliprint's own documented error JSON --
        # this must NOT be classified as a confirmed provider rejection.
        body = b"<html><body>Request blocked by security rules (ref 9f2c)</body></html>"
        exc = _fake_http_error(403, "Forbidden", body, headers={"Content-Type": "text/html", "CF-Ray": "8f0a1b2c-LHR"})
        result = self._send_with_http_error(exc)
        self.assertEqual(result.outcome, OUTCOME_UNKNOWN)
        self.assertIn("did not match Intelliprint's documented", result.message)
        self.assertIn("security rules", result.message)

    def test_401_invalid_key_body_is_rejected_and_distinguishable_from_403(self):
        body = json.dumps({"error": {
            "type": "authentication_error", "code": "invalid_api_key",
            "message": "The API key provided is invalid or has expired.", "param": None,
        }}).encode("utf-8")
        exc = _fake_http_error(401, "Unauthorized", body, headers={"Content-Type": "application/json"})
        result = self._send_with_http_error(exc)
        self.assertEqual(result.outcome, OUTCOME_REJECTED)
        self.assertIn("invalid_api_key", result.message)
        self.assertNotIn("forbidden", result.message)

    def test_api_key_never_appears_in_diagnostic_message(self):
        body = json.dumps({"error": {"type": "authentication_error", "code": "forbidden",
                                       "message": "not authorised", "param": None}}).encode("utf-8")
        exc = _fake_http_error(403, "Forbidden", body, headers={
            "Authorization": "Bearer sk_super_secret_key_value",
            "Set-Cookie": "session=supersecret",
            "X-Api-Key": "sk_super_secret_key_value",
        })
        result = self._send_with_http_error(exc)
        self.assertNotIn("sk_super_secret_key_value", result.message)
        self.assertNotIn("supersecret", result.message)

    def test_disallowed_headers_never_surface_even_when_present(self):
        # Belt-and-braces check on the allow-list itself: Authorization,
        # Set-Cookie, and anything key/token/secret-shaped must never show
        # up in the message, whatever the response actually contains.
        body = json.dumps({"error": {"type": "authentication_error", "code": "forbidden",
                                       "message": "not authorised", "param": None}}).encode("utf-8")
        exc = _fake_http_error(403, "Forbidden", body, headers={
            "Authorization": "Bearer sk_super_secret_key_value",
            "Set-Cookie": "session=abc123",
            "X-Api-Key": "another-secret",
            "X-Auth-Token": "yet-another-secret",
            "Content-Type": "application/json",  # allow-listed: should be present
        })
        result = self._send_with_http_error(exc)
        for forbidden_header_name in ("Authorization", "Set-Cookie", "X-Api-Key", "X-Auth-Token", "abc123", "another-secret", "yet-another-secret"):
            self.assertNotIn(forbidden_header_name, result.message)
        self.assertIn("content-type", result.message.lower())

    def test_network_error_still_maps_to_unknown_not_affected_by_http_error_path(self):
        # Regression guard: a non-HTTPError exception (no real response at
        # all) must still take the old generic-UNKNOWN path, unaffected by
        # the new HTTPError-specific branch.
        result = self._send_with_http_error(TimeoutError("simulated timeout"))
        self.assertEqual(result.outcome, OUTCOME_UNKNOWN)
        self.assertIn("TimeoutError", result.message)


class TestIntelliprintProviderCheckStatus(unittest.TestCase):
    def setUp(self):
        os.environ["INTELLIPRINT_API_KEY"] = "test-key-123"

    def tearDown(self):
        os.environ.pop("INTELLIPRINT_API_KEY", None)

    def test_check_status_reconciles_to_dispatched(self):
        provider = IntelliprintProvider()
        with patch("letter_providers.intelliprint_provider.build_opener") as mock_build_opener:
            mock_build_opener.return_value.open.return_value = _fake_http_response(
                {"id": "print_abc", "letters": [{"id": "letter_1", "status": "sent"}]})
            result = provider.check_status("print_abc")
        self.assertIsNotNone(result)
        self.assertEqual(result.outcome, OUTCOME_DISPATCHED)

    def test_check_status_returns_none_on_network_failure(self):
        provider = IntelliprintProvider()
        with patch("letter_providers.intelliprint_provider.build_opener") as mock_build_opener:
            mock_build_opener.return_value.open.side_effect = TimeoutError("simulated timeout")
            result = provider.check_status("print_abc")
        self.assertIsNone(result)

    def test_check_status_without_credentials_returns_none(self):
        os.environ.pop("INTELLIPRINT_API_KEY", None)
        self.assertIsNone(IntelliprintProvider().check_status("print_abc"))

    def test_check_status_surfaces_pdf_preview_url_too(self):
        provider = IntelliprintProvider()
        with patch("letter_providers.intelliprint_provider.build_opener") as mock_build_opener:
            mock_build_opener.return_value.open.return_value = _fake_http_response({
                "id": "print_abc",
                "letters": [{"id": "letter_1", "status": "sent",
                             "pdf": "https://api.intelliprint.net/files/signed/letter_1.pdf?sig=xyz"}],
            })
            result = provider.check_status("print_abc")
        self.assertIn("https://api.intelliprint.net/files/signed/letter_1.pdf?sig=xyz", result.message)

    def test_check_status_surfaces_pages_sheets_too(self):
        provider = IntelliprintProvider()
        with patch("letter_providers.intelliprint_provider.build_opener") as mock_build_opener:
            mock_build_opener.return_value.open.return_value = _fake_http_response({
                "id": "print_abc", "pages": 2, "sheets": 1,
                "letters": [{"id": "letter_1", "status": "sent"}],
            })
            result = provider.check_status("print_abc")
        self.assertIn("pages=2", result.message)
        self.assertIn("sheets=1", result.message)


class TestRegistryFallbackRules(unittest.TestCase):
    """The load-bearing tests in this file -- see registry.py's module
    docstring for the invariant being locked in here."""

    def setUp(self):
        self.cur = FakeCursor(fetchone_results=[
            None,                    # suppression.is_suppressed -> not suppressed
            ("obligation-1",),       # claim_for_submission -> claimed
        ])

    def test_falls_back_to_second_provider_after_confirmed_rejection(self):
        primary = FakeLetterProvider(force_outcome="rejected")
        primary.name = "primary"
        backup = FakeLetterProvider(force_outcome="accepted")
        backup.name = "backup"
        registry = ProviderRegistry([ProviderSlot(adapter=primary), ProviderSlot(adapter=backup)])

        outcome = attempt_send(self.cur, registry, "obligation-1", worker_id="w1",
                                content_html="<html>x</html>", address_lines=_request().address_lines,
                                applicant_name="J Bloggs", lead_reference="PLANIT-001",
                                idempotency_key="idem-1", is_dry_run=False)
        self.assertEqual(outcome.final_status, "accepted")
        self.assertEqual(outcome.provider_name, "backup")
        self.assertEqual(outcome.attempts_made, 2)

    def test_does_not_fall_back_on_unknown_outcome(self):
        """The single most important behaviour in this file: an ambiguous
        first attempt must STOP the whole thing at 'unknown', never try a
        second provider (which could result in the letter going out twice
        if the first attempt was actually accepted)."""
        primary = FakeLetterProvider(force_outcome="unknown")
        primary.name = "primary"
        backup = FakeLetterProvider(force_outcome="accepted")
        backup.name = "backup"
        registry = ProviderRegistry([ProviderSlot(adapter=primary), ProviderSlot(adapter=backup)])

        outcome = attempt_send(self.cur, registry, "obligation-1", worker_id="w1",
                                content_html="<html>x</html>", address_lines=_request().address_lines,
                                applicant_name="J Bloggs", lead_reference="PLANIT-001",
                                idempotency_key="idem-1", is_dry_run=False)
        self.assertEqual(outcome.final_status, "unknown")
        self.assertEqual(outcome.attempts_made, 1)
        self.assertNotEqual(outcome.provider_name, "backup")

    def test_an_adapter_that_raises_is_treated_as_unknown_not_rejected(self):
        """Covers 'crash after submission' -- an exception must never be
        read as 'safe to try the next provider'."""
        class CrashingAdapter(FakeLetterProvider):
            def send(self, request):
                raise ConnectionError("simulated network crash mid-request")
        crashing = CrashingAdapter()
        crashing.name = "primary"
        backup = FakeLetterProvider(force_outcome="accepted")
        backup.name = "backup"
        registry = ProviderRegistry([ProviderSlot(adapter=crashing), ProviderSlot(adapter=backup)])

        outcome = attempt_send(self.cur, registry, "obligation-1", worker_id="w1",
                                content_html="<html>x</html>", address_lines=_request().address_lines,
                                applicant_name="J Bloggs", lead_reference="PLANIT-001",
                                idempotency_key="idem-1", is_dry_run=False)
        self.assertEqual(outcome.final_status, "unknown")
        self.assertEqual(outcome.attempts_made, 1)

    def test_dry_run_never_calls_any_provider(self):
        primary = FakeLetterProvider()
        primary.name = "primary"
        registry = ProviderRegistry([ProviderSlot(adapter=primary)])
        calls = []
        primary.send = lambda request: calls.append(1) or ProviderResult(outcome=OUTCOME_ACCEPTED, provider_name="primary")

        outcome = attempt_send(self.cur, registry, "obligation-1", worker_id="w1",
                                content_html="<html>x</html>", address_lines=_request().address_lines,
                                applicant_name="J Bloggs", lead_reference="PLANIT-001",
                                idempotency_key="idem-1", is_dry_run=True)
        self.assertEqual(outcome.final_status, "dry_run")
        self.assertEqual(calls, [])

    def test_suppressed_recipient_never_reaches_any_provider(self):
        cur = FakeCursor(fetchone_results=[("row-1", "objected", "this_person")])  # suppressed
        primary = FakeLetterProvider()
        primary.name = "primary"
        calls = []
        primary.send = lambda request: calls.append(1) or ProviderResult(outcome=OUTCOME_ACCEPTED, provider_name="primary")
        registry = ProviderRegistry([ProviderSlot(adapter=primary)])

        outcome = attempt_send(cur, registry, "obligation-1", worker_id="w1",
                                content_html="<html>x</html>", address_lines=_request().address_lines,
                                applicant_name="J Bloggs", lead_reference="PLANIT-001",
                                idempotency_key="idem-1", is_dry_run=False)
        self.assertEqual(outcome.final_status, "suppressed")
        self.assertEqual(calls, [])

    def test_already_claimed_obligation_is_skipped_not_resent(self):
        """Concurrent-worker case: a second worker racing on the same
        obligation must not attempt a send at all."""
        cur = FakeCursor(fetchone_results=[None, None])  # not suppressed, claim fails
        primary = FakeLetterProvider()
        registry = ProviderRegistry([ProviderSlot(adapter=primary)])
        outcome = attempt_send(cur, registry, "obligation-1", worker_id="w2",
                                content_html="<html>x</html>", address_lines=_request().address_lines,
                                applicant_name="J Bloggs", lead_reference="PLANIT-001",
                                idempotency_key="idem-1", is_dry_run=False)
        self.assertEqual(outcome.final_status, "skipped")

    def test_disabled_or_unconfigured_provider_is_never_selected(self):
        unconfigured = IntelliprintProvider()  # is_configured() always False
        backup = FakeLetterProvider(force_outcome="accepted")
        backup.name = "backup"
        registry = ProviderRegistry([ProviderSlot(adapter=unconfigured), ProviderSlot(adapter=backup)])
        outcome = attempt_send(self.cur, registry, "obligation-1", worker_id="w1",
                                content_html="<html>x</html>", address_lines=_request().address_lines,
                                applicant_name="J Bloggs", lead_reference="PLANIT-001",
                                idempotency_key="idem-1", is_dry_run=False)
        self.assertEqual(outcome.provider_name, "backup")

    def test_cost_ceiling_excludes_a_too_expensive_provider(self):
        expensive = FakeLetterProvider(force_outcome="accepted")
        expensive.name = "expensive"
        registry = ProviderRegistry([ProviderSlot(adapter=expensive, cost_ceiling_pence=10)])
        outcome = attempt_send(self.cur, registry, "obligation-1", worker_id="w1",
                                content_html="<html>x</html>", address_lines=_request().address_lines,
                                applicant_name="J Bloggs", lead_reference="PLANIT-001",
                                idempotency_key="idem-1", is_dry_run=False, estimated_cost_pence=45)
        self.assertEqual(outcome.final_status, "failed")

    def test_provider_suspended_after_repeated_non_accept_outcomes(self):
        flaky = FakeLetterProvider(force_outcome="rejected")
        flaky.name = "flaky"
        slot = ProviderSlot(adapter=flaky)
        for outcome in (OUTCOME_REJECTED, OUTCOME_REJECTED, OUTCOME_REJECTED):
            slot.record_outcome(outcome)
        self.assertTrue(slot.suspended)
        self.assertFalse(slot.usable(estimated_cost_pence=None))


if __name__ == "__main__":
    unittest.main()
