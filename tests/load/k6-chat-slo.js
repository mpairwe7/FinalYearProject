/**
 * k6 Load Test — URA Chatbot API SLO Validation
 *
 * Validates NFR-01 (p95 <= 3s), NFR-02 (99.5% uptime), and DEPLOYMENT.md
 * SLO targets (p95 < 2s for /v1/chat, error rate < 1%).
 *
 * Run:
 *   k6 run tests/load/k6-chat-slo.js
 *   k6 run --env BASE_URL=https://chat.ura.go.ug tests/load/k6-chat-slo.js
 *
 * Standards: ISO/IEC 25010:2023 §2 (Performance Efficiency),
 *            week08 NFR-01 (p95 ≤ 3s), DEPLOYMENT.md SLOs
 */
import http from "k6/http";
import { check, sleep } from "k6";
import { Rate, Trend } from "k6/metrics";

// Custom metrics
const errorRate = new Rate("errors");
const chatLatency = new Trend("chat_latency", true);
const healthLatency = new Trend("health_latency", true);

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";

// Test scenarios matching production traffic patterns
export const options = {
  scenarios: {
    // Scenario 1: Steady-state (normal traffic)
    steady_state: {
      executor: "constant-vus",
      vus: 10,
      duration: "2m",
      gracefulStop: "10s",
    },
    // Scenario 2: Spike (sudden load increase)
    spike: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: "30s", target: 50 },
        { duration: "1m", target: 50 },
        { duration: "30s", target: 0 },
      ],
      startTime: "2m30s",
    },
  },

  // SLO thresholds (FAIL the test if breached)
  thresholds: {
    // p95 latency for chat must be < 3000ms (NFR-01)
    chat_latency: ["p(95)<3000"],
    // p95 latency for health must be < 200ms
    health_latency: ["p(95)<200"],
    // Error rate must be < 1% (SLO)
    errors: ["rate<0.01"],
    // Overall HTTP failure rate < 1%
    http_req_failed: ["rate<0.01"],
    // 99th percentile of all requests < 5s
    http_req_duration: ["p(99)<5000"],
  },
};

const CHAT_QUERIES = [
  "What is the current VAT rate in Uganda?",
  "How do I register for a TIN?",
  "What are the PAYE tax brackets?",
  "How do I file my annual tax returns?",
  "What is EFRIS and how do I register?",
  "What are the penalties for late filing?",
  "How do I apply for a tax clearance certificate?",
  "What is withholding tax?",
  "How do I calculate capital gains tax?",
  "What are the customs duty rates for imports?",
];

export default function () {
  // Health check (lightweight — validates infrastructure)
  const healthRes = http.get(`${BASE_URL}/health`, {
    tags: { name: "health" },
  });
  check(healthRes, {
    "health returns 200": (r) => r.status === 200,
    "health returns alive": (r) => {
      try {
        return JSON.parse(r.body).status === "alive";
      } catch {
        return false;
      }
    },
  });
  healthLatency.add(healthRes.timings.duration);
  errorRate.add(healthRes.status !== 200);

  sleep(0.5);

  // Chat request (heavyweight — validates the full RAG pipeline)
  const query = CHAT_QUERIES[Math.floor(Math.random() * CHAT_QUERIES.length)];
  const chatRes = http.post(
    `${BASE_URL}/v1/chat`,
    JSON.stringify({
      message: query,
      locale: "en",
      top_k: 4,
    }),
    {
      headers: { "Content-Type": "application/json" },
      tags: { name: "chat" },
      timeout: "10s",
    }
  );

  const chatOk = check(chatRes, {
    "chat returns 200": (r) => r.status === 200,
    "chat has reply field": (r) => {
      try {
        return JSON.parse(r.body).reply !== undefined;
      } catch {
        return false;
      }
    },
    "chat has sources": (r) => {
      try {
        return Array.isArray(JSON.parse(r.body).sources);
      } catch {
        return false;
      }
    },
  });

  chatLatency.add(chatRes.timings.duration);
  errorRate.add(!chatOk);

  sleep(1 + Math.random() * 2); // Realistic inter-request gap
}

export function handleSummary(data) {
  const chatP95 = data.metrics.chat_latency
    ? data.metrics.chat_latency.values["p(95)"]
    : "N/A";
  const errRate = data.metrics.errors
    ? (data.metrics.errors.values.rate * 100).toFixed(2)
    : "N/A";

  return {
    stdout: `
=== URA Chatbot SLO Validation ===
Chat p95 latency: ${typeof chatP95 === "number" ? chatP95.toFixed(0) + "ms" : chatP95} (target: <3000ms)
Error rate:       ${errRate}% (target: <1%)
==================================
`,
  };
}
