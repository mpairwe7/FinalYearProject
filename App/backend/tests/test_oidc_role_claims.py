"""Role resolution from real-provider claim shapes.

Verified end-to-end against a live Keycloak 26 (authorization-code + PKCE S256,
RS256 tokens from the realm's JWKS). That run proved the shape this file pins:
Keycloak sends roles as ``realm_access.roles`` and never as a flat ``role``, so
resolving only the flat claim silently downgraded every real officer to "public".

These are the vocabulary edge cases a single provider run does not reach —
foreign role names, several roles at once, group-path formatting, and the
override — plus the regression that our own dev tokens keep working.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
import unittest
import unittest.mock as mock

# Env must be set before importing app.* (read at import time).
os.environ.setdefault("LLM_ENABLED", "false")
os.environ.setdefault("SPEECH_ENABLED", "false")
os.environ.setdefault("QDRANT_ENABLED", "false")
os.environ.setdefault("OTEL_ENABLED", "false")

from app.auth import dependencies as deps  # noqa: E402
from app.auth.jwt_auth import (  # noqa: E402
    JWTAuthError,
    JWTVerifier,
    _b64url_encode,
    make_dev_token,
)


class TestResolveRole(unittest.TestCase):
    """`resolve_role` maps provider claims onto the AuthUser role enum."""

    def test_flat_role_claim_still_works(self) -> None:
        # Our own make_dev_token shape. Everything already in the suite depends
        # on this, so provider support must not cost it.
        for role in ("public", "verified_taxpayer", "ura_staff", "ura_admin", "ura_auditor"):
            self.assertEqual(deps.resolve_role({"role": role}), role)

    def test_keycloak_realm_access_roles(self) -> None:
        # The shape the live Keycloak run actually produced.
        claims = {"realm_access": {"roles": ["ura_admin"]}}
        self.assertEqual(deps.resolve_role(claims), "ura_admin")

    def test_keycloak_realm_roles_alongside_provider_defaults(self) -> None:
        # A real realm also carries its own vocabulary; it must be ignored rather
        # than reaching the Literal on AuthUser.role.
        claims = {
            "realm_access": {
                "roles": ["offline_access", "default-roles-ura", "uma_authorization", "ura_staff"]
            }
        }
        self.assertEqual(deps.resolve_role(claims), "ura_staff")

    def test_keycloak_client_roles_under_the_audience(self) -> None:
        claims = {"resource_access": {"ura-chatbot": {"roles": ["ura_auditor"]}}}
        self.assertEqual(deps.resolve_role(claims, audience="ura-chatbot"), "ura_auditor")

    def test_client_roles_for_another_client_are_ignored(self) -> None:
        # Holding a role at a different client says nothing about access here.
        claims = {"resource_access": {"some-other-app": {"roles": ["ura_admin"]}}}
        self.assertEqual(deps.resolve_role(claims, audience="ura-chatbot"), "public")

    def test_entra_style_roles_array(self) -> None:
        self.assertEqual(deps.resolve_role({"roles": ["ura_staff"]}), "ura_staff")

    def test_group_paths_and_hyphens_are_normalised(self) -> None:
        # Keycloak and Entra emit groups as paths, and hyphens are common where
        # our enum uses underscores.
        self.assertEqual(deps.resolve_role({"groups": ["/ura-admin"]}), "ura_admin")
        self.assertEqual(deps.resolve_role({"groups": ["URA_Auditor"]}), "ura_auditor")

    def test_unknown_vocabulary_degrades_to_public(self) -> None:
        # AuthUser.role is a Literal; an unmapped value used to reach it and
        # raise ValidationError, turning a legitimate sign-in into a 500.
        for claims in (
            {"role": "administrator"},
            {"roles": ["Domain Admins"]},
            {"realm_access": {"roles": ["superuser"]}},
            {"groups": ["/finance"]},
            {},
        ):
            self.assertEqual(deps.resolve_role(claims), "public")

    def test_widest_role_wins_regardless_of_claim_order(self) -> None:
        # An admin who also works the queue must not lose the admin view because
        # of how the provider happened to order the list.
        self.assertEqual(
            deps.resolve_role({"realm_access": {"roles": ["ura_staff", "ura_admin"]}}),
            "ura_admin",
        )
        self.assertEqual(
            deps.resolve_role({"realm_access": {"roles": ["ura_admin", "ura_staff"]}}),
            "ura_admin",
        )

    def test_malformed_claims_do_not_raise(self) -> None:
        # A provider that sends the wrong type must fail closed, not 500.
        for claims in (
            {"realm_access": "not-a-dict"},
            {"realm_access": {"roles": "ura_admin"}},  # string, not list
            {"roles": [{"nested": "object"}]},
            {"groups": None},
        ):
            self.assertIsInstance(deps.resolve_role(claims), str)
        # The string-not-list case is still a legitimate single role.
        self.assertEqual(deps.resolve_role({"realm_access": {"roles": "ura_admin"}}), "ura_admin")

    def test_auth0_permissions_claim(self) -> None:
        # Auth0 with RBAC + "add permissions to the access token".
        self.assertEqual(deps.resolve_role({"permissions": ["ura_staff"]}), "ura_staff")

    def test_auth0_namespaced_claim_contains_dots(self) -> None:
        # Auth0 requires custom claims to be namespaced as a URI, so the claim
        # NAME contains dots and slashes. Splitting it as a dot-path would look
        # for an "https://ura" key and find nothing.
        claims = {"https://ura.go.ug/roles": ["ura_admin"]}
        with mock.patch.object(deps, "OIDC_ROLE_CLAIM", "https://ura.go.ug/roles"):
            self.assertEqual(deps.resolve_role(claims), "ura_admin")

    def test_literal_claim_name_wins_over_path_walk(self) -> None:
        # A literal key must be preferred, otherwise a nested claim could shadow
        # a namespaced one that happens to share a prefix.
        claims = {
            "a.b": ["ura_admin"],
            "a": {"b": ["ura_staff"]},
        }
        with mock.patch.object(deps, "OIDC_ROLE_CLAIM", "a.b"):
            self.assertEqual(deps.resolve_role(claims), "ura_admin")

    def test_nested_path_still_resolves_when_no_literal_key(self) -> None:
        claims = {"a": {"b": ["ura_staff"]}}
        with mock.patch.object(deps, "OIDC_ROLE_CLAIM", "a.b"):
            self.assertEqual(deps.resolve_role(claims), "ura_staff")

    def test_explicit_claim_path_override(self) -> None:
        claims = {"ura": {"access": {"role": ["ura_auditor"]}}}
        with mock.patch.object(deps, "OIDC_ROLE_CLAIM", "ura.access.role"):
            self.assertEqual(deps.resolve_role(claims), "ura_auditor")

    def test_override_wins_when_the_claim_is_present(self) -> None:
        # An explicit path means "roles live here"; a stray flat claim elsewhere
        # must not grant access the configured path does not.
        claims = {"role": "ura_admin", "ura": {"access": {"role": ["ura_staff"]}}}
        with mock.patch.object(deps, "OIDC_ROLE_CLAIM", "ura.access.role"):
            self.assertEqual(deps.resolve_role(claims), "ura_staff")

    def test_present_but_empty_override_grants_nothing(self) -> None:
        # An empty list is a real answer — "this user holds no roles" — and must
        # not fall through to a claim that would grant access.
        claims = {"role": "ura_admin", "https://ura.go.ug/roles": []}
        with mock.patch.object(deps, "OIDC_ROLE_CLAIM", "https://ura.go.ug/roles"):
            self.assertEqual(deps.resolve_role(claims), "public")

    def test_absent_override_falls_back_to_the_defaults(self) -> None:
        # Setting the variable before the provider mapping exists (or misspelling
        # it) must not lock every officer out. The signed token already prevents
        # claim injection, so probing the standard claims costs nothing.
        claims = {"permissions": ["ura_admin"]}
        with mock.patch.object(deps, "OIDC_ROLE_CLAIM", "https://ura.go.ug/roles"):
            self.assertEqual(deps.resolve_role(claims), "ura_admin")

    def test_absent_override_with_no_roles_anywhere_is_public(self) -> None:
        with mock.patch.object(deps, "OIDC_ROLE_CLAIM", "https://ura.go.ug/roles"):
            self.assertEqual(deps.resolve_role({"sub": "abc"}), "public")


class TestClaimsToUser(unittest.TestCase):
    """The translation layer builds a valid AuthUser from provider claims."""

    def test_keycloak_claims_produce_a_staff_user(self) -> None:
        claims = {
            "sub": "f8d17790-9a49-42d3-8d80-1273fb63e621",
            "iss": "http://127.0.0.1:8180/realms/ura",
            "aud": "ura-chatbot",
            "email": "officer.admin@ura.go.ug",
            "preferred_username": "officer.admin",
            "realm_access": {"roles": ["ura_admin"]},
            "iat": 1_760_000_000,
            "exp": 1_760_003_600,
        }
        user = deps._claims_to_user(claims)
        self.assertEqual(user.role, "ura_admin")
        self.assertTrue(user.is_staff)
        self.assertEqual(user.email, "officer.admin@ura.go.ug")
        self.assertEqual(user.user_id, claims["sub"])

    def test_provider_user_with_no_mapped_role_is_not_staff(self) -> None:
        user = deps._claims_to_user({"sub": "abc", "email": "jane@example.ug"})
        self.assertEqual(user.role, "public")
        self.assertFalse(user.is_staff)

    def test_foreign_role_does_not_raise_validation_error(self) -> None:
        # Regression: this path used to pass the raw string into the Literal.
        user = deps._claims_to_user({"sub": "abc", "role": "Global Administrator"})
        self.assertEqual(user.role, "public")

    def test_dev_token_round_trip(self) -> None:
        # The dev-token path the dashboards fall back to when no IdP is configured.
        token = make_dev_token("ops", role="ura_staff", email="ops@ura.go.ug")
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        user = deps._claims_to_user(claims)
        self.assertEqual(user.role, "ura_staff")
        self.assertTrue(user.is_staff)
        self.assertEqual(user.email, "ops@ura.go.ug")


class TestTemporalAndAlgorithmClaims(unittest.TestCase):
    """Verifier rejections that the live-provider probes cannot reach.

    Signature verification runs before the temporal claims, so a forged token
    never gets as far as `exp`. These control the signing key so the expiry and
    algorithm paths are actually exercised.
    """

    def _verifier(self, **over: object) -> JWTVerifier:
        base: dict[str, object] = {
            "alg": "HS256",
            "dev_secret": "test-secret",
            "issuer": "ura-chatbot-dev",
            "audience": "ura-chatbot",
        }
        base.update(over)
        return JWTVerifier(**base)  # type: ignore[arg-type]

    def _token(self, secret: str = "test-secret", **over: object) -> str:
        now = int(time.time())
        claims: dict[str, object] = {
            "sub": "officer",
            "iss": "ura-chatbot-dev",
            "aud": "ura-chatbot",
            "iat": now,
            "nbf": now - 5,
            "exp": now + 3600,
            "realm_access": {"roles": ["ura_admin"]},
        }
        claims.update(over)
        header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
        payload = _b64url_encode(json.dumps(claims).encode())
        sig = hmac.new(secret.encode(), f"{header}.{payload}".encode(), hashlib.sha256).digest()
        return f"{header}.{payload}.{_b64url_encode(sig)}"

    def test_valid_token_verifies(self) -> None:
        claims = self._verifier().verify(self._token())
        self.assertEqual(deps.resolve_role(claims), "ura_admin")

    def test_expired_token_rejected(self) -> None:
        with self.assertRaises(JWTAuthError) as caught:
            self._verifier().verify(self._token(exp=int(time.time()) - 10))
        self.assertIn("expired", str(caught.exception))

    def test_not_yet_valid_token_rejected(self) -> None:
        with self.assertRaises(JWTAuthError) as caught:
            self._verifier().verify(self._token(nbf=int(time.time()) + 600))
        self.assertIn("not yet valid", str(caught.exception))

    def test_wrong_audience_rejected(self) -> None:
        # A token minted for another client of the same issuer must not be
        # accepted here — this is what the Keycloak audience mapper exists for.
        with self.assertRaises(JWTAuthError) as caught:
            self._verifier().verify(self._token(aud="some-other-app"))
        self.assertIn("audience", str(caught.exception))

    def test_wrong_issuer_rejected(self) -> None:
        with self.assertRaises(JWTAuthError) as caught:
            self._verifier().verify(self._token(iss="http://evil.test/realms/ura"))
        self.assertIn("issuer", str(caught.exception))

    def test_bad_signature_rejected(self) -> None:
        with self.assertRaises(JWTAuthError) as caught:
            self._verifier().verify(self._token(secret="not-the-secret"))
        self.assertIn("signature", str(caught.exception))

    def test_rs256_verifier_refuses_an_hs256_token(self) -> None:
        # Algorithm confusion: a symmetric token must never be accepted by a
        # verifier configured for the provider's asymmetric keys. Confirmed
        # against the live backend too, but pinned here so it cannot regress.
        verifier = self._verifier(alg="RS256", jwks_url="http://127.0.0.1:1/nope")
        with self.assertRaises(JWTAuthError) as caught:
            verifier.verify(self._token())
        self.assertIn("unexpected alg", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
