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


class TestReceptionistSerializer(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.serializer = BrowserCallSerializer()

    async def test_serialize_output_audio(self):
        pcm = b"\x01\x00" * 320
        frame = OutputAudioRawFrame(audio=pcm, sample_rate=16000, num_channels=1)
        res = await self.serializer.serialize(frame)
        self.assertEqual(res, pcm)

    async def test_serialize_interruption(self):
        frame = InterruptionFrame()
        res = await self.serializer.serialize(frame)
        self.assertIsNotNone(res)
        data = json.loads(res)
        self.assertEqual(data.get("type"), "interrupt")

    async def test_serialize_transport_message(self):
        msg = {"type": "status", "status": "transferring"}
        frame = OutputTransportMessageFrame(message=msg)
        res = await self.serializer.serialize(frame)
        self.assertIsNotNone(res)
        data = json.loads(res)
        self.assertEqual(data.get("status"), "transferring")

    async def test_deserialize_pcm_bytes(self):
        pcm = b"\x02\x00" * 320
        frame = await self.serializer.deserialize(pcm)
        self.assertIsInstance(frame, InputAudioRawFrame)
        self.assertEqual(frame.audio, pcm)

    async def test_deserialize_request_officer(self):
        raw = json.dumps({"type": "request_officer", "reason": "help"})
        frame = await self.serializer.deserialize(raw)
        self.assertIsInstance(frame, RequestOfficerFrame)
        self.assertEqual(frame.reason, "help")

    async def test_deserialize_ping(self):
        raw = json.dumps({"type": "ping"})
        frame = await self.serializer.deserialize(raw)
        self.assertIsNone(frame)

    async def test_deserialize_interrupt(self):
        raw = json.dumps({"type": "interrupt"})
        frame = await self.serializer.deserialize(raw)
        self.assertIsInstance(frame, InterruptionFrame)
