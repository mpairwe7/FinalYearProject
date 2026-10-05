"""RFC 3161 trusted timestamps for audit seals.

A seal proves the ledger agrees with itself; it cannot prove *when* it was
made, or that someone with database access did not rewrite the rows and the
seal together. A timestamp token from an independent Time-Stamping Authority
can: the TSA signs (hash, time) with its own key, so a token whose imprint
matches the seal shows the seal existed, unchanged, at that time.

This module speaks the protocol itself rather than pulling in a CMS stack:

* :func:`request_timestamp` sends a DER ``TimeStampReq`` (SHA-256 imprint, a
  random nonce, ``certReq``) as ``application/timestamp-query`` and checks the
  ``TimeStampResp`` status, imprint and nonce before accepting it;
* :func:`verify_token` checks a stored token from scratch: the ``TSTInfo``
  imprint, the CMS signed attributes (content type and message digest), the
  signature with the TSA certificate carried in the token, the
  signing-certificate binding (ESSCertID/v2), the ``timeStamping`` extended key
  usage, the certificate's validity at ``genTime`` and — when trust anchors are
  configured (``AUDIT_TSA_CA_CERT``) — the chain up to one of them.

Supported signatures: RSA PKCS#1 v1.5 and ECDSA with SHA-1/256/384/512
(what public TSAs issue). RSASSA-PSS tokens are reported as unsupported
rather than accepted.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import logging
import os
import secrets
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

#: Prefix of a token stored by this module. Tokens written before it (the
#: earlier JSON client stored whatever the endpoint returned) carry no prefix
#: and are not treated as evidence.
TOKEN_PREFIX = "rfc3161:v1:"

OID_SHA1 = "1.3.14.3.2.26"
OID_SHA256 = "2.16.840.1.101.3.4.2.1"
OID_SHA384 = "2.16.840.1.101.3.4.2.2"
OID_SHA512 = "2.16.840.1.101.3.4.2.3"
OID_SIGNED_DATA = "1.2.840.113549.1.7.2"
OID_TST_INFO = "1.2.840.113549.1.9.16.1.4"
OID_ATTR_CONTENT_TYPE = "1.2.840.113549.1.9.3"
OID_ATTR_MESSAGE_DIGEST = "1.2.840.113549.1.9.4"
OID_ATTR_SIGNING_CERT = "1.2.840.113549.1.9.16.2.12"
OID_ATTR_SIGNING_CERT_V2 = "1.2.840.113549.1.9.16.2.47"
OID_KP_TIME_STAMPING = "1.3.6.1.5.5.7.3.8"
OID_RSA_PSS = "1.2.840.113549.1.1.10"

_HASH_BY_OID = {
    OID_SHA1: "sha1",
    OID_SHA256: "sha256",
    OID_SHA384: "sha384",
    OID_SHA512: "sha512",
    # Signature algorithms that imply their hash.
    "1.2.840.113549.1.1.5": "sha1",
    "1.2.840.113549.1.1.11": "sha256",
    "1.2.840.113549.1.1.12": "sha384",
    "1.2.840.113549.1.1.13": "sha512",
    "1.2.840.10045.4.1": "sha1",
    "1.2.840.10045.4.3.2": "sha256",
    "1.2.840.10045.4.3.3": "sha384",
    "1.2.840.10045.4.3.4": "sha512",
}
_HASH_OID = {"sha256": OID_SHA256, "sha384": OID_SHA384, "sha512": OID_SHA512, "sha1": OID_SHA1}


class TimestampError(Exception):
    """The token or response is malformed, mismatched or untrusted."""


# ---------------------------------------------------------------------------
# Minimal DER
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Node:
    """One DER element: its identifier octet, contents and full encoding."""

    tag: int
    content: bytes
    raw: bytes

    def children(self) -> list[Node]:
        return parse_all(self.content)


def _parse_one(data: bytes, offset: int) -> tuple[Node, int]:
    if offset + 2 > len(data):
        raise TimestampError("truncated DER element")
    tag = data[offset]
    if tag & 0x1F == 0x1F:
        raise TimestampError("high-tag-number DER form is not supported")
    first = data[offset + 1]
    pos = offset + 2
    if first & 0x80:
        size = first & 0x7F
        if size == 0 or size > 4:
            raise TimestampError("indefinite or oversized DER length")
        length = int.from_bytes(data[pos : pos + size], "big")
        pos += size
    else:
        length = first
    end = pos + length
    if end > len(data):
        raise TimestampError("truncated DER content")
    return Node(tag, data[pos:end], data[offset:end]), end


def parse_all(data: bytes) -> list[Node]:
    nodes: list[Node] = []
    offset = 0
    while offset < len(data):
        node, offset = _parse_one(data, offset)
        nodes.append(node)
    return nodes


def parse_single(data: bytes) -> Node:
    node, end = _parse_one(data, 0)
    if end != len(data):
        raise TimestampError("trailing bytes after DER element")
    return node


def _expect(node: Node, tag: int, what: str) -> Node:
    if node.tag != tag:
        raise TimestampError(f"{what}: expected tag 0x{tag:02x}, found 0x{node.tag:02x}")
    return node


def decode_int(node: Node) -> int:
    return int.from_bytes(_expect(node, 0x02, "INTEGER").content, "big", signed=True)


def decode_oid(node: Node) -> str:
    content = _expect(node, 0x06, "OBJECT IDENTIFIER").content
    if not content:
        raise TimestampError("empty OBJECT IDENTIFIER")
    arcs: list[int] = []
    value = 0
    for byte in content:
        value = (value << 7) | (byte & 0x7F)
        if not byte & 0x80:
            arcs.append(value)
            value = 0
    first = min(arcs[0] // 40, 2)
    return ".".join(str(arc) for arc in [first, arcs[0] - 40 * first, *arcs[1:]])


def decode_generalized_time(node: Node) -> dt.datetime:
    text = _expect(node, 0x18, "GeneralizedTime").content.decode("ascii")
    if not text.endswith("Z"):
        raise TimestampError("GeneralizedTime must be UTC")
    base, _, fraction = text[:-1].partition(".")
    parsed = dt.datetime.strptime(base, "%Y%m%d%H%M%S").replace(tzinfo=dt.timezone.utc)  # noqa: UP017
    if fraction:
        parsed += dt.timedelta(microseconds=int((fraction + "000000")[:6]))
    return parsed


def _length(size: int) -> bytes:
    if size < 0x80:
        return bytes([size])
    encoded = size.to_bytes((size.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(encoded)]) + encoded


def der(tag: int, content: bytes) -> bytes:
    return bytes([tag]) + _length(len(content)) + content


def der_int(value: int) -> bytes:
    return der(0x02, value.to_bytes(value.bit_length() // 8 + 1, "big", signed=True))


def der_oid(dotted: str) -> bytes:
    arcs = [int(part) for part in dotted.split(".")]
    body = bytearray()
    for arc in [40 * arcs[0] + arcs[1], *arcs[2:]]:
        chunk = [arc & 0x7F]
        arc >>= 7
        while arc:
            chunk.append(0x80 | (arc & 0x7F))
            arc >>= 7
        body.extend(reversed(chunk))
    return der(0x06, bytes(body))


def der_null() -> bytes:
    return b"\x05\x00"


def der_bool(value: bool) -> bytes:
    return der(0x01, b"\xff" if value else b"\x00")


def der_octets(value: bytes) -> bytes:
    return der(0x04, value)


def der_seq(*parts: bytes) -> bytes:
    return der(0x30, b"".join(parts))


def der_set(*parts: bytes) -> bytes:
    """DER ``SET OF``: elements in ascending order of their encodings."""
    return der(0x31, b"".join(sorted(parts)))


def algorithm_identifier(oid: str) -> bytes:
    return der_seq(der_oid(oid), der_null())


# ---------------------------------------------------------------------------
# Request / response
# ---------------------------------------------------------------------------
def seal_statement(*, tenant_id: str, first_seq: int, last_seq: int, merkle_root: str, head_hash: str) -> bytes:
    """The bytes a seal's timestamp covers: range, Merkle root and chain head."""
    return (
        f"ura-audit-seal:v1|tenant={tenant_id}|first={first_seq}|last={last_seq}"
        f"|merkle_root={merkle_root}|head_hash={head_hash}"
    ).encode("utf-8")


def build_request(digest: bytes, nonce: int) -> bytes:
    """DER ``TimeStampReq`` (RFC 3161 §2.4.1) for a SHA-256 *digest*."""
    if len(digest) != 32:
        raise ValueError("a SHA-256 digest is 32 bytes")
    imprint = der_seq(algorithm_identifier(OID_SHA256), der_octets(digest))
    return der_seq(der_int(1), imprint, der_int(nonce), der_bool(True))


def parse_response(response: bytes) -> bytes:
    """Return the ``TimeStampToken`` DER from a granted ``TimeStampResp``."""
    elements = parse_single(response).children()
    if not elements:
        raise TimestampError("empty TimeStampResp")
    status_fields = _expect(elements[0], 0x30, "PKIStatusInfo").children()
    status = decode_int(status_fields[0])
    if status not in (0, 1):  # granted, grantedWithMods
        raise TimestampError(f"TSA refused the request (PKIStatus {status})")
    if len(elements) < 2:
        raise TimestampError("granted response without a TimeStampToken")
    return elements[1].raw


@dataclass(frozen=True)
class TimestampInfo:
    """What a verified token says."""

    gen_time: dt.datetime
    serial_number: int
    policy: str
    tsa_subject: str
    chain_verified: bool


def _hash(name: str, data: bytes) -> bytes:
    return hashlib.new(name, data).digest()


def _parse_tst_info(der_bytes: bytes) -> dict[str, Any]:
    fields = parse_single(der_bytes).children()
    if len(fields) < 5:
        raise TimestampError("TSTInfo is missing required fields")
    imprint = _expect(fields[2], 0x30, "MessageImprint").children()
    algorithm = decode_oid(imprint[0].children()[0])
    info: dict[str, Any] = {
        "policy": decode_oid(fields[1]),
        "imprint_algorithm": algorithm,
        "imprint": _expect(imprint[1], 0x04, "hashedMessage").content,
        "serial": decode_int(fields[3]),
        "gen_time": decode_generalized_time(fields[4]),
        "nonce": None,
    }
    for extra in fields[5:]:
        if extra.tag == 0x02:
            info["nonce"] = decode_int(extra)
    return info


def _load_certificates(nodes: list[Node]) -> list[Any]:
    from cryptography import x509

    certs = []
    for node in nodes:
        if node.tag == 0x30:  # Certificate; other CertificateChoices are skipped
            certs.append(x509.load_der_x509_certificate(node.raw))
    return certs


def _find_signer(signer_id: Node, certs: list[Any]) -> Any:
    from cryptography import x509

    if signer_id.tag == 0x30:  # IssuerAndSerialNumber
        issuer_der, serial = signer_id.children()[0].raw, decode_int(signer_id.children()[1])
        for cert in certs:
            if cert.serial_number == serial and cert.issuer.public_bytes() == issuer_der:
                return cert
    elif signer_id.tag == 0x80:  # [0] SubjectKeyIdentifier
        for cert in certs:
            try:
                ski = cert.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value.digest
            except x509.ExtensionNotFound:
                continue
            if ski == signer_id.content:
                return cert
    raise TimestampError("the token does not carry its signing certificate")


def _check_signing_certificate_attr(attributes: dict[str, Node], signer_der: bytes) -> None:
    """ESSCertID (v1: SHA-1) / ESSCertIDv2 (default SHA-256) must name the signer.

    Attribute value layout: ``SigningCertificate[V2] ::= SEQUENCE { certs
    SEQUENCE OF ESSCertID[v2], policies OPTIONAL }``; the first ESSCertID is
    the signer's.
    """
    if OID_ATTR_SIGNING_CERT_V2 in attributes:
        fields = _first_ess_cert_id(attributes[OID_ATTR_SIGNING_CERT_V2])
        algorithm = "sha256"
        if fields and fields[0].tag == 0x30:  # hashAlgorithm present (DEFAULT sha256)
            algorithm = _HASH_BY_OID.get(decode_oid(fields[0].children()[0]), "")
            fields = fields[1:]
        if not algorithm:
            raise TimestampError("unsupported ESSCertIDv2 hash")
    elif OID_ATTR_SIGNING_CERT in attributes:
        fields = _first_ess_cert_id(attributes[OID_ATTR_SIGNING_CERT])
        algorithm = "sha1"
    else:
        raise TimestampError("signed attributes lack the signing-certificate binding")
    if not fields:
        raise TimestampError("empty ESSCertID")
    expected = _expect(fields[0], 0x04, "certHash").content
    if _hash(algorithm, signer_der) != expected:
        raise TimestampError("signing-certificate attribute does not match the signer")


def _first_ess_cert_id(attribute_values: Node) -> list[Node]:
    signing_certificate = attribute_values.children()[0]
    ess_cert_ids = signing_certificate.children()[0].children()
    if not ess_cert_ids:
        raise TimestampError("signing-certificate attribute lists no certificate")
    return ess_cert_ids[0].children()


def _verify_signature(cert: Any, signature: bytes, data: bytes, hash_name: str, sig_oid: str) -> None:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, padding, rsa

    if sig_oid == OID_RSA_PSS:
        raise TimestampError("RSASSA-PSS timestamp signatures are not supported")
    algorithm = {"sha1": hashes.SHA1, "sha256": hashes.SHA256, "sha384": hashes.SHA384, "sha512": hashes.SHA512}[
        hash_name
    ]()
    key = cert.public_key()
    try:
        if isinstance(key, rsa.RSAPublicKey):
            key.verify(signature, data, padding.PKCS1v15(), algorithm)
        elif isinstance(key, ec.EllipticCurvePublicKey):
            key.verify(signature, data, ec.ECDSA(algorithm))
        else:
            raise TimestampError(f"unsupported TSA key type {type(key).__name__}")
    except InvalidSignature as exc:
        raise TimestampError("the TSA signature does not verify") from exc


def _check_tsa_certificate(cert: Any, gen_time: dt.datetime) -> None:
    from cryptography import x509

    try:
        usage = cert.extensions.get_extension_for_class(x509.ExtendedKeyUsage).value
    except x509.ExtensionNotFound as exc:
        raise TimestampError("TSA certificate has no extended key usage") from exc
    if x509.ObjectIdentifier(OID_KP_TIME_STAMPING) not in usage:
        raise TimestampError("TSA certificate is not authorised for timeStamping")
    if not cert.not_valid_before_utc <= gen_time <= cert.not_valid_after_utc:
        raise TimestampError("TSA certificate was not valid at genTime")


def _chain_to_anchor(cert: Any, intermediates: list[Any], anchors: list[Any]) -> None:
    anchor_prints = {anchor.fingerprint(_sha256_hash()) for anchor in anchors}
    current = cert
    for _ in range(6):
        if current.fingerprint(_sha256_hash()) in anchor_prints:
            return
        for issuer in [*anchors, *intermediates]:
            if issuer.subject != current.issuer or issuer is current:
                continue
            try:
                current.verify_directly_issued_by(issuer)
            except Exception:
                continue
            if issuer.fingerprint(_sha256_hash()) in anchor_prints:
                return
            current = issuer
            break
        else:
            raise TimestampError("the TSA certificate does not chain to a configured trust anchor")
    raise TimestampError("TSA certificate chain is too long")


def _sha256_hash() -> Any:
    from cryptography.hazmat.primitives import hashes

    return hashes.SHA256()


def load_trust_anchors(path: str | None = None) -> list[Any]:
    """PEM certificates from ``AUDIT_TSA_CA_CERT`` (a bundle file), or ``[]``."""
    target = path if path is not None else os.getenv("AUDIT_TSA_CA_CERT", "")
    if not target:
        return []
    from cryptography import x509

    with open(target, "rb") as fh:
        return list(x509.load_pem_x509_certificates(fh.read()))


def verify_token(token: bytes, digest: bytes, *, nonce: int | None = None, anchors: list[Any] | None = None) -> TimestampInfo:
    """Verify a DER ``TimeStampToken`` over SHA-256 *digest*; raise :class:`TimestampError`."""
    content_info = parse_single(token).children()
    if decode_oid(content_info[0]) != OID_SIGNED_DATA:
        raise TimestampError("token is not CMS SignedData")
    signed_data = parse_single(_expect(content_info[1], 0xA0, "[0] content").content).children()
    encap = _expect(signed_data[2], 0x30, "EncapsulatedContentInfo").children()
    if decode_oid(encap[0]) != OID_TST_INFO:
        raise TimestampError("token does not encapsulate a TSTInfo")
    tst_der = parse_single(_expect(encap[1], 0xA0, "[0] eContent").content)
    tst_bytes = _expect(tst_der, 0x04, "eContent").content
    tst = _parse_tst_info(tst_bytes)

    if tst["imprint_algorithm"] != OID_SHA256 or tst["imprint"] != digest:
        raise TimestampError("token imprint does not match the sealed statement")
    if nonce is not None and tst["nonce"] != nonce:
        raise TimestampError("token nonce does not match the request")

    certificates: list[Any] = []
    signer_infos: Node | None = None
    for element in signed_data[3:]:
        if element.tag == 0xA0:
            certificates = _load_certificates(element.children())
        elif element.tag == 0x31:
            signer_infos = element
    if signer_infos is None or not signer_infos.children():
        raise TimestampError("token has no SignerInfo")
    signer = signer_infos.children()[0].children()
    signer_cert = _find_signer(signer[1], certificates)
    digest_name = _HASH_BY_OID.get(decode_oid(signer[2].children()[0]), "")
    if not digest_name:
        raise TimestampError("unsupported SignerInfo digest algorithm")

    index = 3
    if index >= len(signer) or signer[index].tag != 0xA0:
        raise TimestampError("RFC 3161 tokens must carry signed attributes")
    signed_attrs = signer[index]
    attributes = {decode_oid(attr.children()[0]): attr.children()[1] for attr in signed_attrs.children()}
    content_type = attributes.get(OID_ATTR_CONTENT_TYPE)
    if content_type is None or decode_oid(content_type.children()[0]) != OID_TST_INFO:
        raise TimestampError("signed content-type attribute is not TSTInfo")
    message_digest = attributes.get(OID_ATTR_MESSAGE_DIGEST)
    if message_digest is None or message_digest.children()[0].content != _hash(digest_name, tst_bytes):
        raise TimestampError("signed message-digest does not match the TSTInfo")
    _check_signing_certificate_attr(attributes, signer_cert.public_bytes(_der_encoding()))

    sig_oid = decode_oid(signer[index + 1].children()[0])
    signature = _expect(signer[index + 2], 0x04, "signature").content
    to_verify = b"\x31" + signed_attrs.raw[1:]  # signed over the explicit SET OF
    _verify_signature(signer_cert, signature, to_verify, _HASH_BY_OID.get(sig_oid, digest_name), sig_oid)
    _check_tsa_certificate(signer_cert, tst["gen_time"])

    chain_verified = False
    if anchors:
        _chain_to_anchor(signer_cert, [c for c in certificates if c is not signer_cert], anchors)
        chain_verified = True
    return TimestampInfo(
        gen_time=tst["gen_time"],
        serial_number=tst["serial"],
        policy=tst["policy"],
        tsa_subject=signer_cert.subject.rfc4514_string(),
        chain_verified=chain_verified,
    )


def _der_encoding() -> Any:
    from cryptography.hazmat.primitives.serialization import Encoding

    return Encoding.DER


def request_timestamp(statement: bytes, tsa_url: str, *, timeout_s: float = 10.0) -> str:
    """Timestamp *statement* at *tsa_url*; return the stored form of the token.

    Verifies the response before accepting it (status, imprint, nonce,
    signature, and the trust chain when anchors are configured), so a seal
    never stores a token it could not later stand behind.
    """
    import httpx

    digest = hashlib.sha256(statement).digest()
    nonce = secrets.randbits(63)
    response = httpx.post(
        tsa_url,
        content=build_request(digest, nonce),
        headers={
            "Content-Type": "application/timestamp-query",
            "Accept": "application/timestamp-reply",
        },
        timeout=timeout_s,
    )
    response.raise_for_status()
    token = parse_response(response.content)
    verify_token(token, digest, nonce=nonce, anchors=load_trust_anchors())
    return TOKEN_PREFIX + base64.b64encode(token).decode("ascii")


def verify_stored_token(stored: str, statement: bytes) -> TimestampInfo | None:
    """Verify a token as stored on a seal; ``None`` for a legacy, pre-RFC 3161 value."""
    if not stored.startswith(TOKEN_PREFIX):
        return None
    try:
        token = base64.b64decode(stored[len(TOKEN_PREFIX) :], validate=True)
    except ValueError as exc:
        raise TimestampError("stored token is not valid base64") from exc
    return verify_token(token, hashlib.sha256(statement).digest(), anchors=load_trust_anchors())
