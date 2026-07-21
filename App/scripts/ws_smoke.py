"""WS smoke for /v2/chat/stream. Verifies the Phase 0-6 surface.

Usage:
    python scripts/ws_smoke.py [ws_url]

Default URL: ws://127.0.0.1:18080/v2/chat/stream (container behind nginx).
"""

import asyncio
import json
import sys
import time

import websockets


async def main(uri: str) -> None:
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({
            "type": "session_start",
            "conversation_id": "smoke-1",
            "locale": "en",
            "protocol_version": 1,
        }))
        ready = json.loads(await ws.recv())
        assert ready["type"] == "session_ready", ready
        print(f"[handshake] session_id={ready['session_id'][:8]} resume={ready['resume']}")
        print(f"[handshake] capabilities={ready['capabilities']}")

        t0 = time.perf_counter()
        await ws.send(json.dumps({
            "type": "response.create",
            "input": "How do I register for a TIN?",
            "top_k": 4,
        }))
        types: list[str] = []
        while True:
            frame = json.loads(await asyncio.wait_for(ws.recv(), timeout=60))
            types.append(frame["type"])
            if frame["type"] == "response.done":
                break
        print(f"[turn 1] frames={types}")
        print(f"[turn 1] ttlb={(time.perf_counter()-t0)*1000:.0f}ms")
        assert "response.retrieval.started" in types
        assert "response.retrieval.completed" in types
        assert "response.done" in types

        # Phase 5: protocol completeness
        await ws.send(json.dumps({"type": "response.create_partial", "input": "vat for"}))
        ack = json.loads(await ws.recv())
        assert ack["type"] == "response.create_partial.ack", ack
        print(f"[partial] ack length={ack['length']}")

        # Phase 0: ping/pong
        await ws.send(json.dumps({"type": "ping"}))
        pong = json.loads(await ws.recv())
        assert pong["type"] == "pong", pong
        print("[ping] pong received")

        # Phase 4: bogus confirm rejected
        await ws.send(json.dumps({
            "type": "tool_call.confirm",
            "confirm_token": "fake.token",
            "call_id": "tc_x",
            "idempotency_key": "ik_x",
            "decision": "approve",
        }))
        rej = json.loads(await ws.recv())
        assert rej["type"] == "response.tool_call.confirm_failed", rej
        print(f"[confirm] bogus token rejected: {rej['reason']}")

        await ws.send(json.dumps({"type": "session_end"}))
        print("\nAll WS assertions passed.")


if __name__ == "__main__":
    uri = sys.argv[1] if len(sys.argv) > 1 else "ws://127.0.0.1:18080/v2/chat/stream"
    asyncio.run(main(uri))
