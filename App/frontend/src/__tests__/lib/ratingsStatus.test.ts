/**
 * What the "Found helpful" card says when it shows no figure (CodeRabbit on
 * PR 731): while the ratings request is still on its way it said "No answers
 * have been rated in this period", which nothing had established yet.
 */
import { describe, expect, it } from "vitest";

import { ratingsEmptyText } from "../../lib/ratingsStatus";

describe("ratingsEmptyText", () => {
  it("says the ratings are loading while the request is pending", () => {
    expect(ratingsEmptyText({ isError: false, isPending: true, data: undefined })).toMatch(/loading/i);
  });

  it("says nothing was rated only after a response with no ratings", () => {
    expect(ratingsEmptyText({ isError: false, isPending: false, data: { total: 0 } })).toBe(
      "No answers have been rated in this period.",
    );
  });

  it("says the ratings could not be loaded on an error", () => {
    expect(ratingsEmptyText({ isError: true, isPending: false, data: undefined })).toMatch(/could not be loaded/i);
  });
});
