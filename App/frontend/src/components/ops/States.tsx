"use client";

import React from "react";
import { AlertTriangleIcon, InboxEmptyIcon } from "./icons";

/**
 * Loading, empty and error states for the console.
 *
 * The staff pages used to say "Loading the queue…", "Loading flags…",
 * "Checking…" and "Loading dashboard data..." — four bare sentences that reserve
 * none of the space the real content will take, so every load ended in a jump.
 * A skeleton that mirrors the final layout costs nothing and removes the shift.
 *
 * An empty queue and a failed request also stopped looking alike: "nothing is
 * waiting" is good news, "we could not reach the backend" is not, and a grey
 * paragraph said both in the same voice.
 */

export function Skeleton({
  width = "100%",
  height = 14,
  radius,
  className = "",
}: {
  width?: number | string;
  height?: number | string;
  radius?: number | string;
  className?: string;
}) {
  return (
    <span
      className={`ops-skeleton${className ? ` ${className}` : ""}`}
      style={{ width, height, borderRadius: radius }}
      aria-hidden="true"
    />
  );
}

/** Placeholder rows shaped like the list they stand in for. */
export function SkeletonRows({ rows = 4, height = 56 }: { rows?: number; height?: number }) {
  return (
    <div className="ops-stack-2" aria-hidden="true">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} height={height} radius="var(--ops-radius-sm)" />
      ))}
    </div>
  );
}

export function SkeletonStats({ count = 6, cols = 3 }: { count?: number; cols?: number }) {
  return (
    <div
      className="ops-stat-grid"
      aria-hidden="true"
      style={{ "--ops-stat-cols": cols } as React.CSSProperties}
    >
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="ops-stat">
          <Skeleton width="60%" height={10} />
          <Skeleton width="40%" height={26} />
          <Skeleton width="80%" height={10} />
        </div>
      ))}
    </div>
  );
}

export function EmptyState({
  title,
  body,
  action,
  icon,
}: {
  title: string;
  body?: React.ReactNode;
  action?: React.ReactNode;
  icon?: React.ReactNode;
}) {
  return (
    <div className="ops-empty">
      <span className="ops-empty-mark" aria-hidden="true">
        {icon ?? <InboxEmptyIcon />}
      </span>
      <p className="ops-empty-title">{title}</p>
      {body ? <p className="ops-empty-body">{body}</p> : null}
      {action}
    </div>
  );
}

export function ErrorState({
  title = "Could not load this",
  body,
  onRetry,
}: {
  title?: string;
  body?: React.ReactNode;
  onRetry?: () => void;
}) {
  return (
    <div className="ops-error" role="alert">
      <span style={{ color: "var(--ops-bad)", flex: "none", marginTop: 1 }} aria-hidden="true">
        <AlertTriangleIcon />
      </span>
      <div style={{ minWidth: 0, flex: 1 }}>
        <p className="ops-error-title">{title}</p>
        {body ? <p className="ops-error-body">{body}</p> : null}
      </div>
      {onRetry ? (
        <button type="button" className="ops-btn is-sm" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  );
}
