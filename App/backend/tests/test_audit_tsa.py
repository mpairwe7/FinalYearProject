"""RFC 3161 timestamps on audit seals (app.audit.tsa).

The previous client POSTed JSON (no TSA speaks that), stored any 200 body as
the "token", and verification only checked the token was 8+ characters. These
tests build real CMS-signed tokens with a local test TSA and check every step
the verifier now performs. A live check against public TSAs (DigiCert,
Sectigo) is recorded in docs/runbooks/audit-trail.md.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import unittest
import unittest.mock as mock
import uuid

os.environ.setdefault("ANALYTICS_BACKEND", "sqlite")
os.environ.setdefault("OTEL_ENABLED", "false")

from cryptography import x509  # noqa: E402
from cryptography.hazmat.primitives import hashes, serialization  # noqa: E402
from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa  # noqa: E402
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID  # noqa: E402

from app import database as db  # noqa: E402
from app.audit import tsa  # noqa: E402
from app.audit.tsa import (  # noqa: E402
    OID_ATTR_CONTENT_TYPE,
    OID_ATTR_MESSAGE_DIGEST,
    OID_ATTR_SIGNING_CERT_V2,
    OID_SHA256,
    OID_SIGNED_DATA,
    OID_TST_INFO,
    TimestampError,
    der,
    der_int,
    der_octets,
    der_oid,
    der_seq,
    der_set,
)
from app.flags import flags  # noqa: E402

NOW = dt.datetime(2026, 10, 6, 12, 0, tzinfo=dt.timezone.utc)  # noqa: UP017
DER = serialization.Encoding.DER


def setUpModule() -> None:
    db.init_db()


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def make_ca(cn: str = "Test Root CA") -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (
        x509.CertificateBuilder()
        .subject_name(_name(cn))
        .issuer_name(_name(cn))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - dt.timedelta(days=365))
        .not_valid_after(NOW + dt.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    return key, cert


def make_tsa_cert(
    ca: tuple[rsa.RSAPrivateKey, x509.Certificate],
    key: object,
    *,
    eku: bool = True,
    not_after: dt.datetime | None = None,
) -> x509.Certificate:
    ca_key, ca_cert = ca
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name("Test TSA"))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())  # type: ignore[attr-defined]
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - dt.timedelta(days=30))
        .not_valid_after(not_after or NOW + dt.timedelta(days=365))
    )
    if eku:
        builder = builder.add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), critical=True)
    return builder.sign(ca_key, hashes.SHA256())


def make_issuer(
    parent: tuple[rsa.RSAPrivateKey, x509.Certificate],
    cn: str,
    *,
    ca: bool | None = True,
    path_length: int | None = None,
) -> tuple[rsa.RSAPrivateKey, x509.Certificate]:
    """A certificate under *parent*; ``ca=None`` leaves out basicConstraints (an end entity)."""
    parent_key, parent_cert = parent
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(cn))
        .issuer_name(parent_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - dt.timedelta(days=100))
        .not_valid_after(NOW + dt.timedelta(days=1000))
    )
    if ca is not None:
        builder = builder.add_extension(x509.BasicConstraints(ca=ca, path_length=path_length), critical=True)
    return key, builder.sign(parent_key, hashes.SHA256())


def _alg(oid: str, null: bool = True) -> bytes:
    return der_seq(der_oid(oid), b"\x05\x00") if null else der_seq(der_oid(oid))


def make_token(
    key: object,
    cert: x509.Certificate,
    digest: bytes,
    *,
    nonce: int | None = 42,
    gen_time: dt.datetime = NOW,
    chain: tuple[x509.Certificate, ...] = (),
    signing_cert_hash: bytes | None = None,
    corrupt_signature: bool = False,
) -> bytes:
    cert_der = cert.public_bytes(DER)
    tst_fields = [
        der_int(1),
        der_oid("1.3.6.1.4.1.99999.1"),
        der_seq(_alg(OID_SHA256), der_octets(digest)),
        der_int(1234567),
        der(0x18, gen_time.strftime("%Y%m%d%H%M%SZ").encode("ascii")),
    ]
    if nonce is not None:
        tst_fields.append(der_int(nonce))
    tst = der_seq(*tst_fields)
    attrs = der_set(
        der_seq(der_oid(OID_ATTR_CONTENT_TYPE), der_set(der_oid(OID_TST_INFO))),
        der_seq(der_oid(OID_ATTR_MESSAGE_DIGEST), der_set(der_octets(hashlib.sha256(tst).digest()))),
        der_seq(
            der_oid(OID_ATTR_SIGNING_CERT_V2),
            der_set(der_seq(der_seq(der_seq(der_octets(signing_cert_hash or hashlib.sha256(cert_der).digest()))))),
        ),
    )
    if isinstance(key, rsa.RSAPrivateKey):
        signature = key.sign(attrs, padding.PKCS1v15(), hashes.SHA256())
        sig_alg = _alg("1.2.840.113549.1.1.11")
    else:
        signature = key.sign(attrs, ec.ECDSA(hashes.SHA256()))  # type: ignore[union-attr]
        sig_alg = _alg("1.2.840.10045.4.3.2", null=False)
    if corrupt_signature:
        signature = signature[:-1] + bytes([signature[-1] ^ 0x01])
    signer_info = der_seq(
        der_int(1),
        der_seq(cert.issuer.public_bytes(), der_int(cert.serial_number)),
        _alg(OID_SHA256),
        b"\xa0" + attrs[1:],
        sig_alg,
        der_octets(signature),
    )
    certificates = der(0xA0, cert_der + b"".join(c.public_bytes(DER) for c in chain))
    signed_data = der_seq(
        der_int(3),
        der_set(_alg(OID_SHA256)),
        der_seq(der_oid(OID_TST_INFO), der(0xA0, der_octets(tst))),
        certificates,
        der_set(signer_info),
    )
    return der_seq(der_oid(OID_SIGNED_DATA), der(0xA0, signed_data))


class VerifyTokenTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ca = make_ca()
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.cert = make_tsa_cert(cls.ca, cls.key)
        cls.digest = hashlib.sha256(b"seal statement").digest()

    def test_valid_rsa_token_with_trusted_chain(self) -> None:
        token = make_token(self.key, self.cert, self.digest)
        info = tsa.verify_token(token, self.digest, nonce=42, anchors=[self.ca[1]])
        self.assertTrue(info.chain_verified)
        self.assertEqual(info.gen_time, NOW)
        self.assertEqual(info.serial_number, 1234567)

    def test_valid_ecdsa_token(self) -> None:
        key = ec.generate_private_key(ec.SECP256R1())
        cert = make_tsa_cert(self.ca, key)
        info = tsa.verify_token(make_token(key, cert, self.digest), self.digest, anchors=[self.ca[1]])
        self.assertTrue(info.chain_verified)

    def test_without_anchors_signature_is_checked_but_chain_is_not(self) -> None:
        info = tsa.verify_token(make_token(self.key, self.cert, self.digest), self.digest)
        self.assertFalse(info.chain_verified)

    def _rejects(self, token: bytes, *, digest: bytes | None = None, nonce: int | None = None, anchors=None) -> str:
        with self.assertRaises(TimestampError) as ctx:
            tsa.verify_token(token, digest or self.digest, nonce=nonce, anchors=anchors or [self.ca[1]])
        return str(ctx.exception)

    def test_other_statement_is_rejected(self) -> None:
        msg = self._rejects(make_token(self.key, self.cert, self.digest), digest=hashlib.sha256(b"other").digest())
        self.assertIn("imprint", msg)

    def test_nonce_mismatch_is_rejected(self) -> None:
        self.assertIn("nonce", self._rejects(make_token(self.key, self.cert, self.digest), nonce=7))

    def test_bad_signature_is_rejected(self) -> None:
        token = make_token(self.key, self.cert, self.digest, corrupt_signature=True)
        self.assertIn("signature", self._rejects(token))

    def test_certificate_without_timestamping_usage_is_rejected(self) -> None:
        cert = make_tsa_cert(self.ca, self.key, eku=False)
        self.assertIn("extended key usage", self._rejects(make_token(self.key, cert, self.digest)))

    def test_certificate_expired_at_gen_time_is_rejected(self) -> None:
        cert = make_tsa_cert(self.ca, self.key, not_after=NOW - dt.timedelta(days=1))
        self.assertIn("not valid at genTime", self._rejects(make_token(self.key, cert, self.digest)))

    def test_untrusted_issuer_is_rejected(self) -> None:
        other_ca = make_ca("Other Root")
        msg = self._rejects(make_token(self.key, self.cert, self.digest), anchors=[other_ca[1]])
        self.assertIn("trust anchor", msg)

    def test_chain_through_an_intermediate(self) -> None:
        root = make_ca("Root")
        inter_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        inter = (
            x509.CertificateBuilder()
            .subject_name(_name("Intermediate"))
            .issuer_name(root[1].subject)
            .public_key(inter_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(NOW - dt.timedelta(days=100))
            .not_valid_after(NOW + dt.timedelta(days=1000))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
            .sign(root[0], hashes.SHA256())
        )
        cert = make_tsa_cert((inter_key, inter), self.key)
        token = make_token(self.key, cert, self.digest, chain=(inter,))
        self.assertTrue(tsa.verify_token(token, self.digest, anchors=[root[1]]).chain_verified)

    def test_an_end_entity_cannot_act_as_an_intermediate(self) -> None:
        """A trusted root's ordinary certificate holder must not be able to mint a TSA."""
        root = make_ca("Root")
        for ca in (None, False):
            rogue = make_issuer(root, "Not a CA", ca=ca)
            cert = make_tsa_cert(rogue, self.key)
            token = make_token(self.key, cert, self.digest, chain=(rogue[1],))
            self.assertIn("not a CA", self._rejects(token, anchors=[root[1]]))

    def test_path_length_constraint_is_enforced(self) -> None:
        root = make_ca("Root")
        upper = make_issuer(root, "Upper", path_length=0)
        lower = make_issuer(upper, "Lower")
        cert = make_tsa_cert(lower, self.key)
        token = make_token(self.key, cert, self.digest, chain=(lower[1], upper[1]))
        self.assertIn("path length", self._rejects(token, anchors=[root[1]]))

    def test_malformed_token_is_a_timestamp_error(self) -> None:
        token = make_token(self.key, self.cert, self.digest)
        for broken in (der_seq(der_oid(OID_SIGNED_DATA)), token[: len(token) // 2], b"\x30\x00"):
            with self.assertRaises(TimestampError):
                tsa.verify_token(broken, self.digest, anchors=[self.ca[1]])

    def test_signing_certificate_binding_is_checked(self) -> None:
        token = make_token(self.key, self.cert, self.digest, signing_cert_hash=b"\x00" * 32)
        self.assertIn("signing-certificate", self._rejects(token))

    def test_refused_response(self) -> None:
        with self.assertRaises(TimestampError):
            tsa.parse_response(der_seq(der_seq(der_int(2))))

    def test_der_helpers_round_trip(self) -> None:
        self.assertEqual(tsa.decode_oid(tsa.parse_single(der_oid("1.2.840.113549.1.9.16.1.4"))), OID_TST_INFO)
        for value in (0, 1, 127, 128, 255, 256, 2**63 - 1):
            self.assertEqual(tsa.decode_int(tsa.parse_single(der_int(value))), value)


class RequestTimestampTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.ca = make_ca()
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.cert = make_tsa_cert(cls.ca, cls.key)

    def _fake_tsa(self, url: str, content: bytes, headers: dict, timeout: float) -> mock.Mock:
        self.assertEqual(headers["Content-Type"], "application/timestamp-query")
        request = tsa.parse_single(content).children()
        imprint = request[1].children()[1].content
        nonce = tsa.decode_int(request[2])
        token = make_token(self.key, self.cert, imprint, nonce=nonce)
        response = mock.Mock(content=der_seq(der_seq(der_int(0)), token))
        response.raise_for_status = mock.Mock()
        return response

    def test_request_verify_and_store(self) -> None:
        statement = tsa.seal_statement(tenant_id="t", first_seq=1, last_seq=5, merkle_root="ab" * 32, head_hash="cd" * 32)
        with mock.patch("httpx.post", side_effect=self._fake_tsa), mock.patch.object(tsa, "load_trust_anchors", return_value=[self.ca[1]]):
            stored = tsa.request_timestamp(statement, "https://tsa.test/tsr")
            self.assertTrue(stored.startswith(tsa.TOKEN_PREFIX))
            self.assertTrue(tsa.verify_stored_token(stored, statement).chain_verified)
            with self.assertRaises(TimestampError):
                tsa.verify_stored_token(stored, statement + b"!")

    def test_legacy_values_are_not_evidence(self) -> None:
        self.assertIsNone(tsa.verify_stored_token("some-json-junk-from-before", b"statement"))


class SealTimestampTest(unittest.TestCase):
    """The ledger requests a token when sealing and the verifier checks it."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.ca = make_ca()
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.cert = make_tsa_cert(cls.ca, cls.key)

    def setUp(self) -> None:
        flags.set("audit_ledger", True)

    def tearDown(self) -> None:
        flags.clear("audit_ledger")

    def _token_for(self, statement: bytes, url: str) -> str:
        import base64

        token = make_token(self.key, self.cert, hashlib.sha256(statement).digest(), nonce=None)
        return tsa.TOKEN_PREFIX + base64.b64encode(token).decode("ascii")

    def test_seal_is_timestamped_and_verified(self) -> None:
        from app.audit import get_ledger
        from app.audit.verifier import verify_anchor

        tenant = f"tsa-{uuid.uuid4().hex[:8]}"
        ledger = get_ledger()
        ledger.append("generate", {"n": 1}, tenant_id=tenant)
        ledger.append("generate", {"n": 2}, tenant_id=tenant)
        with mock.patch.dict(os.environ, {"AUDIT_TSA_URL": "https://tsa.test/tsr"}), \
             mock.patch.object(tsa, "request_timestamp", side_effect=self._token_for), \
             mock.patch.object(tsa, "load_trust_anchors", return_value=[self.ca[1]]):
            anchor = ledger.seal_pending(tenant)
            self.assertTrue(anchor["tsa_token"].startswith(tsa.TOKEN_PREFIX))
            self.assertIsNone(verify_anchor(anchor, tenant))
            forged = dict(anchor, tsa_token=self._token_for(b"another seal", ""))
            broken = verify_anchor(forged, tenant)
        self.assertIsNotNone(broken)
        self.assertIn("timestamp token", broken.reason)

    def _sealed(self, token_for=None) -> tuple[dict, str]:
        from app.audit import get_ledger

        tenant = f"tsa-{uuid.uuid4().hex[:8]}"
        get_ledger().append("generate", {"n": 1}, tenant_id=tenant)
        with mock.patch.dict(os.environ, {"AUDIT_TSA_URL": "https://tsa.test/tsr"}), \
             mock.patch.object(tsa, "request_timestamp", side_effect=token_for or self._token_for), \
             mock.patch.object(tsa, "load_trust_anchors", return_value=[self.ca[1]]):
            return get_ledger().seal_pending(tenant), tenant

    def test_an_unanchored_token_breaks_the_seal_when_timestamps_are_on(self) -> None:
        """Without trust anchors a self-signed "TSA" would pass the signature check."""
        from app.audit.verifier import verify_anchor

        anchor, tenant = self._sealed()
        with mock.patch.object(tsa, "load_trust_anchors", return_value=[]):
            with mock.patch.dict(os.environ, {"AUDIT_TSA_URL": "https://tsa.test/tsr"}):
                broken = verify_anchor(anchor, tenant)
            with mock.patch.dict(os.environ, {"AUDIT_TSA_URL": ""}):
                self.assertIsNone(verify_anchor(anchor, tenant))
        self.assertIn("trust anchor", broken.reason)

    def test_required_mode_breaks_on_a_missing_or_legacy_token(self) -> None:
        from app.audit.verifier import verify_anchor

        anchor, tenant = self._sealed()
        cases = {"blank": "", "legacy": "freeform-value-from-before-rfc3161"}
        for label, value in cases.items():
            forged = dict(anchor, tsa_token=value)
            with mock.patch.dict(os.environ, {"AUDIT_TSA_REQUIRED": ""}):
                self.assertIsNone(verify_anchor(forged, tenant), label)
            with mock.patch.dict(os.environ, {"AUDIT_TSA_REQUIRED": "true"}):
                self.assertIsNotNone(verify_anchor(forged, tenant), label)
        with mock.patch.dict(os.environ, {"AUDIT_TSA_REQUIRED": "true"}), \
             mock.patch.object(tsa, "load_trust_anchors", return_value=[self.ca[1]]):
            self.assertIsNone(verify_anchor(anchor, tenant))

    def test_a_malformed_stored_token_is_a_break_not_a_crash(self) -> None:
        import base64

        from app.audit.verifier import verify_anchor

        anchor, tenant = self._sealed()
        garbage = tsa.TOKEN_PREFIX + base64.b64encode(der_seq(der_oid(OID_SIGNED_DATA))).decode("ascii")
        broken = verify_anchor(dict(anchor, tsa_token=garbage), tenant)
        self.assertIn("malformed", broken.reason)

    def test_unreachable_tsa_still_seals_and_is_counted(self) -> None:
        from app.analytics import metrics
        from app.audit import get_ledger

        tenant = f"tsa-{uuid.uuid4().hex[:8]}"
        get_ledger().append("generate", {"n": 1}, tenant_id=tenant)
        key = 'audit_tsa_failures_total{reason="ConnectError"}'
        before = metrics.snapshot()["counters"].get(key, 0)

        class ConnectError(Exception):
            pass

        with mock.patch.dict(os.environ, {"AUDIT_TSA_URL": "https://tsa.test/tsr"}), \
             mock.patch.object(tsa, "request_timestamp", side_effect=ConnectError("down")):
            anchor = get_ledger().seal_pending(tenant)
        self.assertEqual(anchor["tsa_token"], "")
        self.assertEqual(metrics.snapshot()["counters"].get(key, 0) - before, 1)


if __name__ == "__main__":
    unittest.main()
