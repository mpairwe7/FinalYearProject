from __future__ import annotations

import unittest
from app.auth.vault import TokenVault, get_token_vault


class TokenVaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.vault = TokenVault(master_key="test-vault-secret-key-12345")

    def test_encrypt_decrypt_roundtrip(self) -> None:
        raw_payload = "dummy-connector-sample-xyz-123"  # pragma: allowlist secret
        ciphertext = self.vault.encrypt_secret(raw_payload)
        self.assertNotEqual(raw_payload, ciphertext)
        self.assertTrue(len(ciphertext) > len(raw_payload))

        decrypted = self.vault.decrypt_secret(ciphertext)
        self.assertEqual(raw_payload, decrypted)

    def test_mask_secret(self) -> None:
        sample_input = "sample-token-abcdef1234"  # pragma: allowlist secret
        masked = TokenVault.mask_secret(sample_input, unmasked_suffix=4)
        self.assertTrue(masked.startswith("••••••••"))
        self.assertTrue(masked.endswith("1234"))
        self.assertNotIn("sample-token", masked)

    def test_empty_secret_handling(self) -> None:
        self.assertEqual(self.vault.encrypt_secret(""), "")
        self.assertEqual(self.vault.decrypt_secret(""), "")
        self.assertEqual(TokenVault.mask_secret(""), "")

    def test_tampered_ciphertext_fails_gracefully(self) -> None:
        tampered = "invalid-base64-random-junk"
        decrypted = self.vault.decrypt_secret(tampered)
        self.assertEqual(decrypted, "")


if __name__ == "__main__":
    unittest.main()
