"""Names for people whose access token carries none.

An Auth0 access token for the console's API has ``sub`` and ``permissions``
but no name or email, so the console used to show the account id: a taxpayer
was told "Officer Auth0|6a7d7b1b… has joined". The backend now asks the
provider's ``/userinfo`` once per person (cached) and, failing that, never
shows or says a raw id.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("LLM_ENABLED", "false")
os.environ.setdefault("SPEECH_ENABLED", "false")
os.environ.setdefault("QDRANT_ENABLED", "false")
os.environ.setdefault("OTEL_ENABLED", "false")

from app.auth import userinfo  # noqa: E402
from app.receptionist.desk import officer_display_name  # noqa: E402

AUTH0_SUB = "auth0|6a7d7b1b022607ce8165fc96"
TOKEN_CLAIMS = {"sub": AUTH0_SUB, "iss": "https://tenant.example/", "permissions": ["ura_staff"]}
PROFILE = {
    "sub": AUTH0_SUB,
    "email": "officer.staff@ura.go.ug",
    "name": "officer.staff@ura.go.ug",
    "nickname": "officer.staff",
    "picture": "https://example/avatar.png",
}


class ProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        userinfo.reset_cache()
        self.env = patch.dict(os.environ, {"OIDC_USERINFO_URL": "https://tenant.example/userinfo"}, clear=False)
        self.env.start()
        os.environ.pop("OIDC_USERINFO_ENABLED", None)

    def tearDown(self) -> None:
        self.env.stop()
        userinfo.reset_cache()

    def test_fills_the_display_fields_a_token_lacks(self):
        with patch.object(userinfo, "_fetch", return_value=PROFILE) as fetch:
            claims = userinfo.with_profile("tok", TOKEN_CLAIMS)
        fetch.assert_called_once_with("https://tenant.example/userinfo", "tok", 2.0)
        self.assertEqual(claims["email"], "officer.staff@ura.go.ug")
        self.assertEqual(claims["nickname"], "officer.staff")
        self.assertEqual(claims["permissions"], ["ura_staff"])  # the token's own claims are kept
        self.assertNotIn("picture", claims)  # display fields only

    def test_asks_once_per_person(self):
        with patch.object(userinfo, "_fetch", return_value=PROFILE) as fetch:
            userinfo.with_profile("tok-1", TOKEN_CLAIMS)
            userinfo.with_profile("tok-2", TOKEN_CLAIMS)
        self.assertEqual(fetch.call_count, 1)

    def test_a_token_with_its_own_email_is_left_alone(self):
        own = {**TOKEN_CLAIMS, "email": "nakato@ura.go.ug"}
        with patch.object(userinfo, "_fetch", return_value=PROFILE) as fetch:
            self.assertEqual(userinfo.with_profile("tok", own), own)
        fetch.assert_not_called()

    def test_a_profile_for_another_subject_is_ignored(self):
        with patch.object(userinfo, "_fetch", return_value={**PROFILE, "sub": "auth0|someone-else"}):
            self.assertEqual(userinfo.with_profile("tok", TOKEN_CLAIMS), TOKEN_CLAIMS)

    def test_a_failure_changes_nothing_and_is_not_retried_at_once(self):
        with patch.object(userinfo, "_fetch", side_effect=OSError("timed out")) as fetch:
            self.assertEqual(userinfo.with_profile("tok", TOKEN_CLAIMS), TOKEN_CLAIMS)
            self.assertEqual(userinfo.with_profile("tok", TOKEN_CLAIMS), TOKEN_CLAIMS)
        self.assertEqual(fetch.call_count, 1)

    def test_it_can_be_switched_off(self):
        with patch.dict(os.environ, {"OIDC_USERINFO_ENABLED": "false"}), patch.object(userinfo, "_fetch") as fetch:
            self.assertEqual(userinfo.with_profile("tok", TOKEN_CLAIMS), TOKEN_CLAIMS)
        fetch.assert_not_called()

    def test_the_default_endpoint_is_the_issuers(self):
        os.environ.pop("OIDC_USERINFO_URL", None)
        with patch.dict(os.environ, {"OIDC_ISSUER": "https://tenant.example/"}):
            self.assertEqual(userinfo._url(), "https://tenant.example/userinfo")

    def test_a_plain_http_endpoint_is_refused(self):
        with self.assertRaises(ValueError):
            userinfo._fetch("ftp://tenant.example/userinfo", "tok", 1.0)


class OfficerNameTests(unittest.TestCase):
    def test_a_name_or_email_reads_as_an_officer(self):
        self.assertEqual(officer_display_name("nakato@ura.go.ug"), "Officer Nakato")
        self.assertEqual(officer_display_name("Nakato"), "Officer Nakato")

    def test_an_account_id_is_never_shown_or_spoken(self):
        for handle in (AUTH0_SUB, "google-oauth2|1234567890", "6a7d7b1b022607ce8165fc96", "3f2b1c4e-1111-4222-8333-444455556666"):
            self.assertEqual(officer_display_name(handle), "URA officer", handle)

    def test_the_desk_name_prefers_a_real_name_over_the_email(self):
        from app.main import _desk_officer_name

        def ctx(claims: dict, email: str = "", user_id: str = AUTH0_SUB):
            return SimpleNamespace(claims=claims, user=SimpleNamespace(email=email), user_id=user_id)

        self.assertEqual(_desk_officer_name(ctx({"given_name": "Nakato"}, "n@ura.go.ug")), "Officer Nakato")
        self.assertEqual(_desk_officer_name(ctx({"name": "Grace Nakato"}, "n@ura.go.ug")), "Officer Grace Nakato")
        # Auth0 sets name to the email for a database user: the nickname is used instead.
        self.assertEqual(
            _desk_officer_name(ctx({"name": "officer.staff@ura.go.ug", "nickname": "officer.staff"}, "officer.staff@ura.go.ug")),
            "Officer Officer.staff",
        )
        self.assertEqual(_desk_officer_name(ctx({}, "")), "URA officer")


if __name__ == "__main__":
    unittest.main()
