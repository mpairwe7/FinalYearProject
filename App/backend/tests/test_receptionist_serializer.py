"""Unit tests for BrowserCallSerializer protocol conversions."""

from __future__ import annotations

import json
import unittest

from app.receptionist.serializer import (
    BrowserCallSerializer,
    InputAudioRawFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    OutputTransportMessageFrame,
    RequestOfficerFrame,
)


class TestReceptionistSerializer(unittest.TestCase):
    def setUp(self):
        self.serializer = BrowserCallSerializer()

    def test_serialize_output_audio(self):
        pcm = b"\x01\x00" * 320
        frame = OutputAudioRawFrame(audio=pcm, sample_rate=16000, num_channels=1)
        res = self.serializer.serialize(frame)
        self.assertEqual(res, pcm)

    def test_serialize_interruption(self):
        frame = InterruptionFrame()
        res = self.serializer.serialize(frame)
        self.assertIsNotNone(res)
        data = json.loads(res)
        self.assertEqual(data.get("type"), "interrupt")

    def test_serialize_transport_message(self):
        msg = {"type": "status", "status": "transferring"}
        frame = OutputTransportMessageFrame(message=msg)
        res = self.serializer.serialize(frame)
        self.assertIsNotNone(res)
        data = json.loads(res)
        self.assertEqual(data.get("status"), "transferring")

    def test_deserialize_pcm_bytes(self):
        pcm = b"\x02\x00" * 320
        frame = self.serializer.deserialize(pcm)
        self.assertIsInstance(frame, InputAudioRawFrame)
        self.assertEqual(frame.audio, pcm)

    def test_deserialize_request_officer(self):
        raw = json.dumps({"type": "request_officer", "reason": "help"})
        frame = self.serializer.deserialize(raw)
        self.assertIsInstance(frame, RequestOfficerFrame)
        self.assertEqual(frame.reason, "help")

    def test_deserialize_ping(self):
        raw = json.dumps({"type": "ping"})
        frame = self.serializer.deserialize(raw)
        self.assertIsNone(frame)
