/**
 * The dashboard has to be readable by the people accountable for the service,
 * not only by the people who built it.
 *
 * Reported as "graphs may have to be more accurate and have understanding data
 * that even non-IT personnel can understand". The charts were already accurate
 * and already accessible; what they were not was legible. Every panel was
 * captioned in the vocabulary of the system it monitors — "Chat p95 latency",
 * "retrieval_mode = hybrid", "Endpoint latency" over an axis of URL paths, a
 * unit of milliseconds, "abstention_precision" — so a supervisor deciding
 * whether the assistant is serving taxpayers well could not extract one fact
 * without first being taught what a quantile is.
 *
 * These pin the two properties that fix: the jargon does not come back, and
 * every chart states its own reading rather than leaving the reader to derive
 * it from bar heights.
 */
import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import LatencyChart from "../../../components/charts/LatencyChart";
import RetrievalModeChart from "../../../components/charts/RetrievalModeChart";
import TicketStatusChart from "../../../components/charts/TicketStatusChart";
import SloGaugeCard from "../../../components/charts/SloGaugeCard";
import MetricsTable from "../../../components/charts/MetricsTable";
import { plainSeconds, retrievalModeLabel } from "../../../components/charts/chartTheme";

describe("plainSeconds", () => {
  it("speaks in seconds once milliseconds stop being a unit anyone thinks in", () => {
    expect(plainSeconds(1400)).toBe("1.4 seconds");
    expect(plainSeconds(23_000)).toBe("23 seconds");
  });

  it("keeps milliseconds where seconds would round away the number", () => {
    expect(plainSeconds(420)).toBe("420 milliseconds");
  });

  it("has an answer for a missing measurement", () => {
    expect(plainSeconds(undefined)).toBe("—");
    expect(plainSeconds(Number.NaN)).toBe("—");
  });
});

describe("retrievalModeLabel", () => {
  it("says what happened to the question, not what the column stores", () => {
    expect(retrievalModeLabel("hybrid")).toBe("Found in URA documents");
    expect(retrievalModeLabel("abstained")).toBe("Declined — nothing reliable found");
    expect(retrievalModeLabel("escalated")).toBe("Passed to a URA officer");
  });

  it("leaves an unrecognised mode readable rather than inventing a meaning", () => {
    expect(retrievalModeLabel("some_new_mode")).toBe("some new mode");
  });
});

describe("LatencyChart", () => {
  const latency = {
    "POST|/v1/chat": { p50: 900, p95: 2400, p99: 3100, avg: 1200, count: 500 },
    "POST|/v1/tts": { p50: 300, p95: 800, p99: 1100, avg: 400, count: 120 },
  };

  it("names the task rather than the URL path", () => {
    render(<LatencyChart latency={latency} />);
    expect(screen.getByText("How long each thing takes")).toBeInTheDocument();
    // The path is what the code calls it; "Answering a question" is what it is.
    expect(document.body.textContent).not.toContain("/v1/chat");
  });

  it("explains the quantiles instead of printing them", () => {
    render(<LatencyChart latency={latency} />);
    expect(screen.getByText("19 in 20 are faster")).toBeInTheDocument();
    expect(screen.getByText("Half are faster")).toBeInTheDocument();
  });

  it("states the reading, naming the slowest task and the target", () => {
    render(<LatencyChart latency={latency} />);
    const note = screen.getByText(/The slowest of these is/);
    expect(note).toHaveTextContent("Answering a question");
    expect(note).toHaveTextContent("2.4 seconds");
    expect(note).toHaveTextContent("target of 2 seconds");
  });
});

describe("RetrievalModeChart", () => {
  const counters = {
    'retrieval_mode_total{mode="hybrid"}': 62,
    'retrieval_mode_total{mode="keyword"}': 21,
    'retrieval_mode_total{mode="abstained"}': 17,
  };

  it("labels each slice with what happened to the taxpayer's question", () => {
    render(<RetrievalModeChart counters={counters} />);
    expect(screen.getByRole("heading", { level: 3 })).toHaveTextContent("Where answers came from");
    expect(screen.getAllByText("Found in URA documents").length).toBeGreaterThan(0);
    expect(document.body.textContent).not.toContain("retrieval_mode");
  });

  it("states the share that was actually grounded", () => {
    render(<RetrievalModeChart counters={counters} />);
    expect(screen.getByText(/62% of answers were found in URA documents/)).toBeInTheDocument();
  });
});

describe("SloGaugeCard", () => {
  const base = { value: 1400, target: 2000, unit: "ms", invert: true };

  it("shows the figure in a unit a reader thinks in, and the target in words", () => {
    render(
      <SloGaugeCard
        {...base}
        label="Answer speed"
        term="p95 latency"
        format={(v) => plainSeconds(v)}
        note="19 out of every 20 answers arrive faster than this."
      />,
    );
    expect(screen.getByText("1.4 seconds")).toBeInTheDocument();
    expect(screen.getByText(/On target · target at most 1.4 seconds|On target/)).toBeInTheDocument();
    expect(screen.getByText(/19 out of every 20/)).toBeInTheDocument();
  });

  it("keeps the technical name available without making it the label", () => {
    render(<SloGaugeCard {...base} label="Answer speed" term="p95 latency" />);
    const heading = screen.getByRole("heading", { level: 3 });
    expect(heading).toHaveTextContent("Answer speed");
    expect(heading).toHaveTextContent("p95 latency");
  });

  it("says the state in words, so colour is never carrying it alone", () => {
    render(<SloGaugeCard value={5000} target={2000} unit="ms" invert label="Answer speed" />);
    expect(screen.getByText(/Below target/)).toBeInTheDocument();
  });
});

describe("TicketStatusChart", () => {
  const stats = {
    total: 9,
    open: 3,
    assigned: 2,
    resolved: 4,
    wontfix: 0,
    by_priority: { normal: 9 },
  };

  it("says how many people are still waiting", () => {
    render(<TicketStatusChart stats={stats} />);
    expect(screen.getByText(/3 questions are waiting for an officer/)).toBeInTheDocument();
  });

  it("reports an empty queue as good news rather than a zero", () => {
    render(<TicketStatusChart stats={{ ...stats, open: 0 }} />);
    expect(screen.getByText(/Nothing is waiting/)).toBeInTheDocument();
  });
});

describe("MetricsTable", () => {
  it("names each check by what it measures and explains it in the row", () => {
    render(
      <MetricsTable
        metrics={[
          { name: "abstention_precision", value: 0.75, threshold: 0.5, passed: true },
          { name: "faithfulness", value: 0.4, threshold: 0.6, passed: false },
        ]}
      />,
    );
    expect(screen.getByText("Says when it does not know")).toBeInTheDocument();
    expect(screen.getByText(/declines for the right reason/)).toBeInTheDocument();
    // Pass and fail are words, not only a colour.
    expect(screen.getByText("Pass")).toBeInTheDocument();
    expect(screen.getByText("Fail")).toBeInTheDocument();
  });
});
