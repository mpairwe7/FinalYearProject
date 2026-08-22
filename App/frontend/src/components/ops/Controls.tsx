"use client";

import React from "react";
import { useClientClock } from "../../hooks/useClientClock";
import { RefreshIcon } from "./icons";

/**
 * Small shared controls: period picker, freshness stamp, switch, keyboard hint.
 *
 * The period picker existed only on /analytics while /admin hardcoded 30 days
 * in the hook and then *told the reader* "last 30 days" with no way to change
 * it. The freshness stamp is new everywhere: these pages refetch on 10–60
 * second intervals and nothing on screen said so, which is the difference
 * between "the queue is clear" and "the queue was clear at some point".
 */

export const PERIODS = [7, 14, 30, 90] as const;

export function PeriodPicker({
  days,
  onChange,
  label = "Reporting period",
}: {
  days: number;
  onChange: (days: number) => void;
  label?: string;
}) {
  return (
    <div className="ops-segmented" role="group" aria-label={label}>
      {PERIODS.map((d) => (
        <button
          key={d}
          type="button"
          aria-pressed={days === d}
          onClick={() => onChange(d)}
        >
          {d}d
          <span className="ops-sr-only"> — last {d} days</span>
        </button>
      ))}
    </div>
  );
}

/**
 * "Updated 12s ago" plus a manual refresh.
 *
 * `updatedAt` is a react-query `dataUpdatedAt`. The clock is the shared
 * once-a-minute tick, so the label is stable across the page and the server
 * render (which has no clock) says nothing rather than a time that will not
 * survive hydration.
 */
export function Freshness({
  updatedAt,
  isFetching,
  onRefresh,
}: {
  updatedAt?: number;
  isFetching?: boolean;
  onRefresh?: () => void;
}) {
  const now = useClientClock();
  let text = "";
  if (isFetching) {
    text = "Refreshing…";
  } else if (now && updatedAt) {
    const seconds = Math.max(0, Math.round((now - updatedAt) / 1000));
    text =
      seconds < 60
        ? `Updated ${seconds}s ago`
        : seconds < 3600
          ? `Updated ${Math.round(seconds / 60)}m ago`
          : `Updated ${Math.round(seconds / 3600)}h ago`;
  }

  return (
    <span className="ops-fresh">
      <span
        className={`ops-fresh-dot${isFetching || !updatedAt ? " is-stale" : ""}`}
        aria-hidden="true"
      />
      <span aria-live="polite">{text}</span>
      {onRefresh ? (
        <button
          type="button"
          className="ops-icon-btn"
          onClick={onRefresh}
          aria-label="Refresh now"
          title="Refresh now"
        >
          <RefreshIcon />
        </button>
      ) : null}
    </span>
  );
}

/**
 * An on/off control that looks like one.
 *
 * The flags console shipped a button whose entire label was the word "on" or
 * "off", so the state and the action were the same three characters and
 * neither was a recognised control. This is a switch: `aria-pressed` carries
 * the state, the accessible name carries what it switches.
 */
export function Switch({
  checked,
  onChange,
  label,
  disabled,
  describedBy,
}: {
  checked: boolean;
  onChange: (next: boolean) => void;
  /** What this switches — becomes the accessible name. */
  label: string;
  disabled?: boolean;
  describedBy?: string;
}) {
  return (
    <button
      type="button"
      className="ops-switch"
      role="switch"
      aria-checked={checked}
      aria-label={label}
      aria-describedby={describedBy}
      disabled={disabled}
      onClick={() => onChange(!checked)}
    >
      <span className="ops-switch-track" aria-hidden="true">
        <span className="ops-switch-thumb" />
      </span>
      <span aria-hidden="true">{checked ? "On" : "Off"}</span>
    </button>
  );
}

/** A discoverable keyboard hint: `j` move · `/` search. */
export function KeyHint({ keys, children }: { keys: string[]; children: React.ReactNode }) {
  return (
    <span className="ops-hint">
      {keys.map((key) => (
        <kbd className="ops-kbd" key={key}>
          {key}
        </kbd>
      ))}
      {children}
    </span>
  );
}
