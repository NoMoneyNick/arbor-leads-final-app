"""Oct 22 2026: the missing-intermediate TLS repair in net_utils, tested against
a real local HTTPS server whose certificate chain is built on the fly:
root -> intermediate -> leaf, where the server presents ONLY the leaf (the
exact fault on the council portals) and the leaf's 'CA Issuers' address
points at a second local server that serves the intermediate."""
import datetime
import http.server
import os
import ssl
import tempfile
import threading
import unittest
from unittest import mock

import net_utils

try:
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID, ExtendedKeyUsageOID, AuthorityInformationAccessOID
    HAVE_CRYPTO = True
except Exception:  # pragma: no cover
    HAVE_CRYPTO = False


def _name(cn):
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _make_chain(aia_url, leaf_host="localhost"):
    now = datetime.datetime.now(datetime.timezone.utc)
    nb, na = now - datetime.timedelta(days=1), now + datetime.timedelta(days=30)
    root_k, int_k, leaf_k = (ec.generate_private_key(ec.SECP256R1()) for _ in range(3))

    root = (x509.CertificateBuilder().subject_name(_name("Test Root")).issuer_name(_name("Test Root"))
            .public_key(root_k.public_key()).serial_number(x509.random_serial_number()).not_valid_before(nb).not_valid_after(na)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), True)
            .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), True)
            .sign(root_k, hashes.SHA256()))
    inter = (x509.CertificateBuilder().subject_name(_name("Test Intermediate")).issuer_name(root.subject)
             .public_key(int_k.public_key()).serial_number(x509.random_serial_number()).not_valid_before(nb).not_valid_after(na)
             .add_extension(x509.BasicConstraints(ca=True, path_length=0), True)
             .add_extension(x509.KeyUsage(False, False, False, False, False, True, True, False, False), True)
             .add_extension(x509.SubjectKeyIdentifier.from_public_key(int_k.public_key()), False)
             .sign(root_k, hashes.SHA256()))
    leaf = (x509.CertificateBuilder().subject_name(_name(leaf_host)).issuer_name(inter.subject)
            .public_key(leaf_k.public_key()).serial_number(x509.random_serial_number()).not_valid_before(nb).not_valid_after(na)
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), True)
            .add_extension(x509.KeyUsage(True, False, False, False, False, False, False, False, False), True)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), False)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(leaf_host)]), False)
            .add_extension(x509.AuthorityInformationAccess([x509.AccessDescription(
                AuthorityInformationAccessOID.CA_ISSUERS, x509.UniformResourceIdentifier(aia_url))]), False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(int_k.public_key()), False)
            .sign(int_k, hashes.SHA256()))
    return root, inter, leaf, leaf_k


class _Quiet(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass


@unittest.skipUnless(HAVE_CRYPTO, "cryptography not installed")
class TestTlsRepair(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.servers = []
        net_utils._VERIFIED_BUNDLE.clear()
        net_utils._REPAIR_FAILED_AT.clear()
        net_utils._TLS_ALERT_THROTTLE.clear()
        net_utils._CA_DIR = os.path.join(self.tmp, "ca")

    def tearDown(self):
        for s in self.servers:
            s.shutdown()
            s.server_close()
        net_utils._ROOT_CA_BUNDLE = None

    def _serve(self, handler, tls_ctx=None):
        srv = http.server.HTTPServer(("127.0.0.1", 0), handler)
        if tls_ctx:
            srv.socket = tls_ctx.wrap_socket(srv.socket, server_side=True)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.servers.append(srv)
        return srv.server_address[1]

    def _setup_site(self, serve_intermediate=True, trust_root=True):
        holder = {}

        class AIA(_Quiet):
            def do_GET(self):
                if serve_intermediate:
                    self.send_response(200); self.end_headers()
                    self.wfile.write(holder["inter"].public_bytes(serialization.Encoding.DER))
                else:
                    self.send_response(404); self.end_headers()

        aia_port = self._serve(AIA)
        root, inter, leaf, leaf_k = _make_chain(f"http://127.0.0.1:{aia_port}/inter.der")
        holder["inter"] = inter
        leaf_pem = os.path.join(self.tmp, "leaf.pem")
        key_pem = os.path.join(self.tmp, "key.pem")
        with open(leaf_pem, "wb") as f:                       # LEAF ONLY: the fault under test
            f.write(leaf.public_bytes(serialization.Encoding.PEM))
        with open(key_pem, "wb") as f:
            f.write(leaf_k.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
        root_pem = os.path.join(self.tmp, "root.pem")
        with open(root_pem, "wb") as f:
            f.write(root.public_bytes(serialization.Encoding.PEM))
        if trust_root:
            net_utils._ROOT_CA_BUNDLE = root_pem
        else:
            other, *_ = _make_chain("http://x/")
            other_pem = os.path.join(self.tmp, "other.pem")
            with open(other_pem, "wb") as f:
                f.write(other.public_bytes(serialization.Encoding.PEM))
            net_utils._ROOT_CA_BUNDLE = other_pem

        class Site(_Quiet):
            def do_GET(self):
                self.send_response(200); self.end_headers(); self.wfile.write(b"hello")

        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(leaf_pem, key_pem)
        return self._serve(Site, ctx)

    def test_missing_intermediate_is_repaired_and_verification_stays_on(self):
        port = self._setup_site()
        with mock.patch.object(net_utils, "_alert_tls_fallback") as alert:
            # requests' own CA handling is bypassed by passing our bundle, so
            # make _request start from the module's bundle logic.
            with mock.patch("requests.certs.where", return_value=net_utils._ROOT_CA_BUNDLE):
                res = net_utils.smart_get(f"https://localhost:{port}/", max_retries=0, timeout=10)
        self.assertEqual(res.text, "hello")
        alert.assert_not_called()                                  # no unverified fallback needed
        self.assertIn("localhost", net_utils._VERIFIED_BUNDLE)

    def test_second_request_uses_the_remembered_bundle(self):
        port = self._setup_site()
        with mock.patch.object(net_utils, "_alert_tls_fallback") as alert, \
             mock.patch("requests.certs.where", return_value=net_utils._ROOT_CA_BUNDLE):
            net_utils.smart_get(f"https://localhost:{port}/", max_retries=0, timeout=10)
            with mock.patch.object(net_utils, "_repair_missing_intermediate") as repair:
                res = net_utils.smart_get(f"https://localhost:{port}/", max_retries=0, timeout=10)
        self.assertEqual(res.text, "hello")
        repair.assert_not_called()
        alert.assert_not_called()

    def test_untrusted_chain_is_not_trusted_and_falls_back_with_alert(self):
        port = self._setup_site(trust_root=False)
        with mock.patch.object(net_utils, "_alert_tls_fallback") as alert, \
             mock.patch("requests.certs.where", return_value=net_utils._ROOT_CA_BUNDLE):
            res = net_utils.smart_get(f"https://localhost:{port}/", max_retries=0, timeout=10)
        self.assertEqual(res.text, "hello")                        # old behaviour kept
        alert.assert_called_once()
        self.assertEqual(alert.call_args.args[1], "missing_intermediate")
        self.assertNotIn("localhost", net_utils._VERIFIED_BUNDLE)

    def test_unreachable_issuer_falls_back_with_alert(self):
        port = self._setup_site(serve_intermediate=False)
        with mock.patch.object(net_utils, "_alert_tls_fallback") as alert, \
             mock.patch("requests.certs.where", return_value=net_utils._ROOT_CA_BUNDLE):
            res = net_utils.smart_get(f"https://localhost:{port}/", max_retries=0, timeout=10)
        self.assertEqual(res.text, "hello")
        alert.assert_called_once()

    def test_cause_classification(self):
        c = net_utils.classify_ssl_error
        self.assertEqual(c(Exception("certificate verify failed: unable to get local issuer certificate")), "missing_intermediate")
        self.assertEqual(c(Exception("certificate verify failed: certificate has expired")), "expired")
        self.assertEqual(c(Exception("Hostname mismatch, certificate is not valid for 'x'")), "hostname_mismatch")
        self.assertEqual(c(Exception("self-signed certificate")), "self_signed")
        self.assertEqual(c(Exception("weird")), "other")


if __name__ == "__main__":
    unittest.main()
