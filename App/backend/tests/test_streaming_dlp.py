from __future__ import annotations

import unittest
from app.guardrails import StreamingDLPFilter, user_stated_identifiers


class TestStreamingDLPFilter(unittest.TestCase):
    def test_clean_tokens_stream_through_smoothly(self) -> None:
        dlp = StreamingDLPFilter(buffer_size=15)
        chunks = ["The statutory ", "VAT rate ", "in Uganda is ", "18% under ", "the VAT Act."]
        streamed = []
        for c in chunks:
            out = dlp.process_chunk(c)
            if out:
                streamed.append(out)
        streamed.append(dlp.flush())
        full = "".join(streamed)
        self.assertEqual(full, "".join(chunks))

    def test_split_ug_tin_across_token_boundaries_is_masked(self) -> None:
        dlp = StreamingDLPFilter(buffer_size=20)
        # 10-digit TIN: 1001234567, split across 3 chunks
        chunks = ["Your registered TIN is ", "100", "1234", "567. Please keep it safe."]
        streamed = []
        for c in chunks:
            out = dlp.process_chunk(c)
            if out:
                streamed.append(out)
        streamed.append(dlp.flush())
        full = "".join(streamed)
        self.assertNotIn("1001234567", full)
        self.assertIn("[REDACTED_UG_TIN]", full)

    def test_split_ug_nid_across_boundaries_is_masked(self) -> None:
        dlp = StreamingDLPFilter(buffer_size=24)
        # 14-char Ugandan NIN: CM930039182LIR, split across chunks
        chunks = ["NIN record: ", "CM93", "003918", "2LIR", " has been validated."]
        streamed = []
        for c in chunks:
            out = dlp.process_chunk(c)
            if out:
                streamed.append(out)
        streamed.append(dlp.flush())
        full = "".join(streamed)
        self.assertNotIn("CM930039182LIR", full)
        self.assertIn("[REDACTED_UG_NID]", full)

    def test_official_ura_channels_are_exempt_from_redaction(self) -> None:
        dlp = StreamingDLPFilter(buffer_size=15)
        chunks = ["Contact URA via ", "services@ura.go.ug ", "or visit https://ura.go.ug."]
        streamed = []
        for c in chunks:
            out = dlp.process_chunk(c)
            if out:
                streamed.append(out)
        streamed.append(dlp.flush())
        full = "".join(streamed)
        self.assertIn("services@ura.go.ug", full)
        self.assertNotIn("[REDACTED_EMAIL]", full)

    def test_the_taxpayers_own_tin_is_not_redacted_back_to_them(self) -> None:
        # G143/G154: the stream redacted the TIN the taxpayer had just typed, so a
        # lookup they asked for read "EFRIS holds ... for TIN [REDACTED_UG_TIN]".
        stated = user_stated_identifiers("What does EFRIS hold for TIN 1000000001?")
        dlp = StreamingDLPFilter(buffer_size=20, allowed_values=stated)
        chunks = ["For TIN ", "100000", "0001, EFRIS ", "lists a related TIN 1001234567 as well."]
        streamed = []
        for c in chunks:
            out = dlp.process_chunk(c)
            if out:
                streamed.append(out)
        streamed.append(dlp.flush())
        full = "".join(streamed)
        self.assertIn("1000000001", full)
        self.assertNotIn("1001234567", full)
        self.assertEqual(full.count("[REDACTED_UG_TIN]"), 1)

    def test_a_formatted_tin_the_taxpayer_typed_is_kept_in_any_format(self) -> None:
        stated = user_stated_identifiers("Check EFRIS for my TIN 100-000-0001 please")
        self.assertEqual(stated, ("1000000001",))
        dlp = StreamingDLPFilter(buffer_size=20, allowed_values=stated)
        chunks = ["EFRIS holds a record for TIN ", "100-000-", "0001; the agent's TIN ", "100 123 4567 is not yours."]
        streamed = []
        for c in chunks:
            out = dlp.process_chunk(c)
            if out:
                streamed.append(out)
        streamed.append(dlp.flush())
        full = "".join(streamed)
        self.assertIn("100-000-0001", full)
        self.assertNotIn("100 123 4567", full)
        self.assertIn("[REDACTED_UG_TIN]", full)

    def test_stated_identifiers_are_tin_and_prn_shaped_values(self) -> None:
        # Anything TIN- or PRN-shaped the taxpayer typed may be echoed back; a
        # phone number or a formatted amount is not one of those shapes.
        self.assertEqual(
            user_stated_identifiers("TIN 1000000001, PRN 220000123456, phone 0772140000, UGX 1,500,000"),
            ("1000000001", "220000123456"),
        )
        self.assertEqual(user_stated_identifiers(""), ())


if __name__ == "__main__":
    unittest.main()
