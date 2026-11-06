"""Regression coverage for the remaining November 2026 Grok handoff gaps."""

from __future__ import annotations

from app import service


def test_claim_verifier_failure_is_reported_as_unavailable_and_disclosed(monkeypatch) -> None:
    """G370: a verifier outage must not be mistaken for a passed comparison."""
    from app.guardrails import OutputGuard

    def verifier_down(*_args, **_kwargs):
        raise RuntimeError("claim verifier unavailable")

    class Model:
        @staticmethod
        def _evaluate_response_judge(**_kwargs):
            return {
                "decision": "approve",
                "final_decision": "approve",
                "confidence_band": "high",
                "reasons": [],
            }

    monkeypatch.setattr(service, "verify_claims", verifier_down)
    monkeypatch.setattr(service.HybridRetriever, "compute_faithfulness", lambda *_args, **_kwargs: 0.95)
    guard = OutputGuard()
    monkeypatch.setattr(guard, "should_escalate", lambda *_args, **_kwargs: (False, ""))
    token = service._TURN_GENERATION.set(service._new_generation_record())
    try:
        result = service._apply_output_guards(
            Model(),
            message="What is the VAT rate?",
            reply="The standard VAT rate is 18%. [1]",
            hits=[{"text": "The standard VAT rate is 18%.", "source": "URA"}],
            citations=[{"index": 1, "source": "URA"}],
            conversation_history=None,
            session_id="s1",
            conversation_id="c1",
            output_guard=guard,
        )

        assert result["claim_report"]["decision"] == "unavailable"
        assert result["response_judge"]["claim_verification"]["error"] == "claim_verifier_unavailable"
        assert "claim_check_unavailable" in service._turn_degradations()
        served = service._with_degraded_disclosure(result["reply"], service._turn_degradations())
        assert "could not finish the check" in served
    finally:
        service._TURN_GENERATION.reset(token)
