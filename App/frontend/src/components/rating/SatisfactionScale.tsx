"use client";

/**
 * Five faces, very dissatisfied to very satisfied — the one satisfaction scale
 * the service uses everywhere it asks (a taxpayer rating an officer chat, an
 * officer recording what a caller said at wrap-up), so every rating means the
 * same thing in the audit.
 *
 * A radio group: one tab stop, arrow keys move and select, and each face has a
 * spoken name ("4 of 5, Satisfied"). The face is never the only cue: the
 * chosen level's words show under the row. Labels come from the caller, so the
 * taxpayer's copy is translated and the staff console's stays English.
 */
import React, { useId, useRef } from "react";
import "./satisfactionScale.css";

export const SATISFACTION_FACES = ["😠", "🙁", "😐", "🙂", "😄"] as const;

/** English level names, for the staff console and the audit. */
export const SATISFACTION_LABELS_EN = [
  "Very dissatisfied",
  "Dissatisfied",
  "Neutral",
  "Satisfied",
  "Very satisfied",
] as const;

export function SatisfactionScale({
  value,
  onChange,
  labels = SATISFACTION_LABELS_EN,
  groupLabel,
  disabled = false,
  name,
}: {
  value: number | null;
  onChange: (score: number) => void;
  /** The five level names, lowest first. */
  labels?: readonly string[];
  /** What the group asks ("How satisfied are you?"). */
  groupLabel: string;
  disabled?: boolean;
  /** Set to mark the first face for the dialog's initial focus. */
  name?: string;
}) {
  const labelId = useId();
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  // The roving tab stop: the chosen face, or the middle one before a choice.
  const focusIndex = value != null ? value - 1 : 2;

  const choose = (index: number) => {
    const next = Math.max(0, Math.min(4, index));
    onChange(next + 1);
    refs.current[next]?.focus();
  };

  const onKeyDown = (event: React.KeyboardEvent, index: number) => {
    if (disabled) return;
    if (event.key === "ArrowRight" || event.key === "ArrowUp") {
      event.preventDefault();
      choose(index + 1);
    } else if (event.key === "ArrowLeft" || event.key === "ArrowDown") {
      event.preventDefault();
      choose(index - 1);
    } else if (event.key === "Home") {
      event.preventDefault();
      choose(0);
    } else if (event.key === "End") {
      event.preventDefault();
      choose(4);
    }
  };

  return (
    <div className="sat-scale">
      <span id={labelId} className="sat-scale-label">
        {groupLabel}
      </span>
      <div
        className="sat-scale-row"
        role="radiogroup"
        aria-labelledby={labelId}
        aria-disabled={disabled || undefined}
        data-name={name}
      >
        {SATISFACTION_FACES.map((face, index) => {
          const score = index + 1;
          const checked = value === score;
          return (
            <button
              key={score}
              ref={(el) => {
                refs.current[index] = el;
              }}
              type="button"
              role="radio"
              aria-checked={checked}
              aria-label={`${score} of 5, ${labels[index]}`}
              tabIndex={index === focusIndex ? 0 : -1}
              className={`sat-face${checked ? " is-checked" : ""}`}
              disabled={disabled}
              data-score={score}
              onClick={() => onChange(score)}
              onKeyDown={(event) => onKeyDown(event, index)}
              {...(name && index === focusIndex ? { "data-dialog-initial-focus": true } : {})}
            >
              <span aria-hidden="true" className="sat-face-glyph">
                {face}
              </span>
            </button>
          );
        })}
      </div>
      <div className="sat-scale-ends" aria-hidden="true">
        <span>{labels[0]}</span>
        <span>{labels[4]}</span>
      </div>
      <p className="sat-scale-choice" aria-live="polite">
        {value != null ? labels[value - 1] : " "}
      </p>
    </div>
  );
}
