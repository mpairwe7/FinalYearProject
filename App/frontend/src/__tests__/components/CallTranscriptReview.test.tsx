/**
 * The AI's "was this call helpful?" and the caller's answer are marked in the
 * call transcript, so an officer or auditor reading it knows the answer was a
 * review of the call, not part of the caller's question.
 */
import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { CallTranscript } from "../../components/staff/calls/CallTranscript";
import type { CallTurn } from "../../services/callsApi";

const turn = (seq: number, speaker: CallTurn["speaker"], kind: CallTurn["kind"], text: string): CallTurn => ({
  id: `t${seq}`,
  call_id: "call-1",
  seq,
  speaker,
  kind,
  text,
  low_conf_words: [],
  mean_word_prob: null,
  faithfulness: null,
  latencies: {},
  created_at: 1_790_000_000 + seq,
});

describe("CallTranscript — the spoken review", () => {
  it("labels the review question and the caller's answer", () => {
    render(
      <CallTranscript
        turns={[
          turn(1, "assistant", "review", "Before you go, was this call helpful to you?"),
          turn(2, "caller", "review", "Yes, very helpful"),
          turn(3, "caller", "utterance", "How do I pay PAYE?"),
        ]}
      />,
    );
    expect(screen.getByText("Review question")).toBeInTheDocument();
    expect(screen.getByText("Review answer")).toBeInTheDocument();
    expect(screen.getAllByText(/^Review /)).toHaveLength(2);
  });
});
