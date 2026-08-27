import { describe, expect, it } from "vitest";
import { routeFromMetricKey } from "../../../components/charts/LatencyChart";
import { signedInName } from "../../../lib/roles";

/**
 * Both of these lock in defects that were live on the deployed console and that
 * only a rendered page showed. Neither was caught by a type error or an
 * existing test, because in both cases the code did something reasonable with
 * an input shape that never actually occurs.
 */

describe("routeFromMetricKey", () => {
  // What `metrics.snapshot()` actually returns. The chart previously assumed a
  // `GET|/path` key and stripped it with a regex that never matched, so the
  // whole selector was printed on the X axis — eight of them, rotated and
  // overlapping, reading `method="GET",path="/v1/analytics/dashboard"}`.
  it("pulls the path out of a Prometheus series selector", () => {
    expect(routeFromMetricKey('http_request_duration_ms{method="GET",path="/v1/me"}')).toBe(
      "/v1/me",
    );
    expect(
      routeFromMetricKey('http_request_duration_ms{method="POST",path="/v1/analytics/event"}'),
    ).toBe("/v1/analytics/event");
  });

  it("tolerates whitespace and reordered labels", () => {
    expect(routeFromMetricKey('http_request_duration_ms{ path = "/v1/chat" , method="GET" }')).toBe(
      "/v1/chat",
    );
  });

  it("still handles the pipe-prefixed form the code was written for", () => {
    expect(routeFromMetricKey("GET|/v1/chat")).toBe("/v1/chat");
    expect(routeFromMetricKey("DELETE|/v1/admin/overrides")).toBe("/v1/admin/overrides");
  });

  it("passes a bare path through untouched", () => {
    expect(routeFromMetricKey("/v1/feedback")).toBe("/v1/feedback");
  });

  // Inventing a friendly name for an unrecognised key would be worse than
  // showing the true one, so an unparseable key must survive intact.
  it("returns an unrecognised key rather than mangling it", () => {
    expect(routeFromMetricKey("some_other_metric_total")).toBe("some_other_metric_total");
  });
});

describe("signedInName", () => {
  it("prefers the email when the provider gives one", () => {
    expect(signedInName({ email: "officer.admin@ura.go.ug", role: "ura_admin" })).toBe(
      "officer.admin@ura.go.ug",
    );
  });

  // The live defect: with no email, `who.email || who.external_id` greeted the
  // officer with "Signed in as auth0|6a7d7af3ace1faccc70dc644".
  it("never shows a raw provider subject", () => {
    const name = signedInName({ external_id: "auth0|6a7d7af3ace1faccc70dc644", role: "ura_admin" });
    expect(name).not.toContain("auth0|");
    expect(name).not.toContain("6a7d7af3ace1faccc70dc644");
    // Still distinguishes two accounts on the same role.
    expect(name).toContain("Administrator");
    expect(name).toContain("70dc644".slice(-6));
  });

  it("passes through an identity provider that returns a real username", () => {
    expect(signedInName({ external_id: "j.nakato", role: "ura_staff" })).toBe("j.nakato");
  });

  it("degrades to a phrase rather than an empty string", () => {
    expect(signedInName(null)).toBe("your account");
    expect(signedInName({ role: "ura_staff" })).toBe("your account");
  });
});
