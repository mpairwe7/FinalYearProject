"use client";

/**
 * Shared row/control primitives for the settings panel.
 *
 * Every tab is a list of the same shape — label, one line of explanation, one
 * control on the right — so the shape lives here once. Keeping the controls in
 * one file is also what keeps their semantics consistent: switches are
 * `role="switch"`, segmented pickers are radiogroups with arrow-key movement,
 * and long option lists stay native `<select>` elements because the platform
 * picker beats a hand-rolled listbox on a phone.
 */

import React, { useId, useRef } from "react";

export function SettingsSection({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  const headingId = useId();
  return (
    <section className="setv2-section" aria-labelledby={headingId}>
      <h3 id={headingId} className="setv2-section-title">
        {title}
      </h3>
      {description && <p className="setv2-section-desc">{description}</p>}
      <div className="setv2-rows">{children}</div>
    </section>
  );
}

export function SettingsRow({
  label,
  hint,
  htmlFor,
  children,
  stacked,
}: {
  label: string;
  hint?: React.ReactNode;
  /** Set when the control is a native input, so the label points at it. */
  htmlFor?: string;
  children?: React.ReactNode;
  /** Put the control under the text instead of beside it (wide controls). */
  stacked?: boolean;
}) {
  const Label = htmlFor ? "label" : "span";
  return (
    <div className={`setv2-row${stacked ? " setv2-row-stacked" : ""}`}>
      <span className="setv2-row-text">
        <Label className="setv2-row-label" {...(htmlFor ? { htmlFor } : {})}>
          {label}
        </Label>
        {hint && <span className="setv2-row-hint">{hint}</span>}
      </span>
      {children && <span className="setv2-row-control">{children}</span>}
    </div>
  );
}

export function Toggle({
  checked,
  onChange,
  label,
  disabled,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  /** Accessible name — the visible row label, repeated for assistive tech. */
  label: string;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      className="setv2-switch"
      disabled={disabled}
      onClick={() => onChange(!checked)}
    >
      <span className="setv2-switch-knob" aria-hidden="true" />
    </button>
  );
}

export interface SegmentedOption<T extends string> {
  value: T;
  label: string;
}

export function Segmented<T extends string>({
  value,
  options,
  onChange,
  label,
}: {
  value: T;
  options: readonly SegmentedOption<T>[];
  onChange: (next: T) => void;
  label: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  const move = (from: number, delta: number) => {
    const next = (from + delta + options.length) % options.length;
    onChange(options[next].value);
    refs.current[next]?.focus();
  };

  return (
    <span className="setv2-seg" role="radiogroup" aria-label={label}>
      {options.map((option, i) => {
        const active = option.value === value;
        return (
          <button
            key={option.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={active}
            tabIndex={active ? 0 : -1}
            className={`setv2-seg-opt${active ? " setv2-seg-active" : ""}`}
            onClick={() => onChange(option.value)}
            onKeyDown={(e) => {
              if (e.key === "ArrowRight" || e.key === "ArrowDown") {
                e.preventDefault();
                move(i, 1);
              } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
                e.preventDefault();
                move(i, -1);
              }
            }}
          >
            {option.label}
          </button>
        );
      })}
    </span>
  );
}

export function SelectControl({
  id,
  value,
  onChange,
  options,
  disabled,
}: {
  id: string;
  value: string;
  onChange: (next: string) => void;
  options: readonly { value: string; label: string }[];
  disabled?: boolean;
}) {
  return (
    <select
      id={id}
      className="setv2-select"
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    >
      {options.map((o) => (
        <option key={o.value} value={o.value}>
          {o.label}
        </option>
      ))}
    </select>
  );
}

export function TextControl({
  id,
  value,
  onChange,
  placeholder,
  maxLength,
  disabled,
}: {
  id: string;
  value: string;
  onChange: (next: string) => void;
  placeholder?: string;
  maxLength?: number;
  disabled?: boolean;
}) {
  return (
    <input
      id={id}
      type="text"
      className="setv2-input"
      value={value}
      placeholder={placeholder}
      maxLength={maxLength}
      disabled={disabled}
      autoComplete="off"
      onChange={(e) => onChange(e.target.value)}
    />
  );
}

export function ActionButton({
  onClick,
  children,
  variant = "secondary",
  busy,
  disabled,
}: {
  onClick: () => void;
  children: React.ReactNode;
  variant?: "primary" | "secondary" | "danger";
  busy?: boolean;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      className={`setv2-btn setv2-btn-${variant}`}
      onClick={onClick}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
    >
      {children}
    </button>
  );
}

type NoteKind = "info" | "ok" | "error";

export function StatusNote({ kind, children }: { kind: NoteKind; children: React.ReactNode }) {
  return (
    <p className={`setv2-note setv2-note-${kind}`} role={kind === "error" ? "alert" : "status"}>
      {children}
    </p>
  );
}

/**
 * Why a section has nothing to show, in the person's terms.
 *
 * Four different states all mean "not signed in", and they need different
 * advice: wait, sign in, sign in AGAIN, or come back later. Returns null when
 * signed in, so callers can render it unconditionally.
 */
export function IdentityGate({
  status,
  what,
  children,
}: {
  status: string;
  /** What is unavailable, e.g. "A profile". */
  what: string;
  /** Sign-in links, rendered only when signing in would actually help. */
  children?: React.ReactNode;
}) {
  if (status === "signed-in") return null;
  if (status === "checking") {
    return <StatusNote kind="info">Checking your sign-in…</StatusNote>;
  }
  if (status === "unavailable") {
    return (
      <StatusNote kind="error">
        {what} is stored against your account, and the backend cannot be reached
        to load it right now. Nothing has been lost — try again shortly.
      </StatusNote>
    );
  }
  return (
    <StatusNote kind="info">
      {status === "rejected"
        ? `${what} is stored against an account, and your saved sign-in is no longer valid. `
        : `${what} is stored against an account, so this needs a sign-in. `}
      {children}
    </StatusNote>
  );
}
