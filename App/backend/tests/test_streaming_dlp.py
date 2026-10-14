from __future__ import annotations

import unittest
from app.guardrails import StreamingDLPFilter


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
        # 14-char Ugandan NIN: CM90ABCDE12345F, split across chunks
        chunks = ["NIN record: ", "CM90", "ABCDE", "12345F", " has been validated."]
        streamed = []
        for c in chunks:
            out = dlp.process_chunk(c)
            if out:
                streamed.append(out)
        streamed.append(dlp.flush())
        full = "".join(streamed)
        self.assertNotIn("CM90ABCDE12345F", full)
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


if __name__ == "__main__":
    unittest.main()
