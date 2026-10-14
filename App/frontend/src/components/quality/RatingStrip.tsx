"use client";

/**
 * A conversation's ratings in answer order: ✓ helpful, ✕ not helpful.
 *
 * The shape is the point — "✓ ✓ ✕" says "it went wrong at the third answer"
 * faster than any sentence — so each mark is a glyph and a colour, never a
 * colour alone, and the whole strip has one spoken name that reads the
 * sequence out ("Answer 1 helpful, answer 2 helpful, answer 3 not helpful").
 * The break point carries a ring so it can be found in a long strip.
 */
import React from "react";

export interface StripRating {
  answer: number | null;
  rating: "up" | "down" | null;
}

export function ratingStripLabel(ratings: StripRating[]): string {
  if (!ratings.length) return "No rated answers";
  return ratings
    .map((r, i) => {
      const n = r.answer ?? i + 1;
      const what = r.rating === "up" ? "helpful" : r.rating === "down" ? "not helpful" : "not rated";
      return `${i === 0 ? "Answer" : "answer"} ${n} ${what}`;
    })
    .join(", ");
}

export function RatingStrip({
  ratings,
  breakAnswer,
  compact = false,
}: {
  ratings: StripRating[];
  /** Answer number of the break point, to ring. */
  breakAnswer?: number | null;
  compact?: boolean;
}) {
  return (
    <span className={`qa-strip${compact ? " is-compact" : ""}`} role="img" aria-label={ratingStripLabel(ratings)}>
      {ratings.map((r, i) => {
        const tone = r.rating === "up" ? "is-up" : r.rating === "down" ? "is-down" : "is-none";
        const isBreak = breakAnswer != null && r.answer === breakAnswer;
        return (
          <span key={`${r.answer ?? "x"}-${i}`} className={`qa-strip-mark ${tone}${isBreak ? " is-break" : ""}`} aria-hidden="true">
            {r.rating === "up" ? "✓" : r.rating === "down" ? "✕" : "·"}
          </span>
        );
      })}
    </span>
  );
}
