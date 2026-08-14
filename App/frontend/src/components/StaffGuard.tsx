"use client";

/**
 * Client-side staff gate + shared nav for the operations pages.
 *
 * `/admin/tickets` and `/analytics` shipped with no gating at all: the API
 * enforces `require_admin_access`, so an unauthenticated visitor got 401/403/503
 * and the page rendered empty panels with no explanation. This turns that into a
 * clear "sign in" state and, for a signed-in non-staff user, an honest refusal.
 *
 * This is UX, not security. The gate that matters is server-side —
 * `require_admin_access` in main.py, which needs `AuthUser.is_staff` or a
 * configured operator key. Anyone can edit localStorage; nobody can talk their
 * way past the API. Both layers are needed and neither replaces the other.
 */
import Link from "next/link";
import React, { useEffect, useState, useSyncExternalStore } from "react";
import {
  authHeaders,
  clearAuthToken,
  getAuthToken,
  getServerAuthToken,
  subscribeAuthToken,
} from "../lib/authSession";
import { isStaffRole, roleLabel } from "../lib/roles";
import "./staffGuard.css";

export interface StaffIdentity {
  authenticated: boolean;
  role: string;
  email?: string;
  external_id?: string;
  tenant_id?: string;
}

/**
 * What the /v1/me call established — identity only. Whether that identity is
 * *allowed here* is decided at render from `requireRoles`, which keeps the role
 * rule out of the effect's dependencies: it arrives as a fresh array literal on
 * every parent render, and depending on it refetched /v1/me in an unbounded loop.
 */
type State =
  | { kind: "checking" }
  | { kind: "anonymous" }
  | { kind: "unavailable"; detail: string }
  | { kind: "identified"; who: StaffIdentity };

const NAV = [
  { href: "/admin", label: "Overview", roles: ["ura_admin", "ura_auditor"] },
  { href: "/agent", label: "My queue", roles: ["ura_staff", "ura_admin"] },
  { href: "/admin/tickets", label: "All tickets", roles: ["ura_staff", "ura_admin", "ura_auditor"] },
  { href: "/analytics", label: "Analytics", roles: ["ura_admin", "ura_auditor"] },
];

export function StaffNav({ who, current }: { who: StaffIdentity; current: string }) {
  return (
    <nav className="staff-nav" aria-label="Operations">
      <span className="staff-brand">
        <span className="staff-brand-mark" aria-hidden="true">
          URA
        </span>
        Operations
      </span>
      <ul>
        {NAV.filter((item) => item.roles.includes(who.role)).map((item) => (
          <li key={item.href}>
            <a
              href={item.href}
              className={item.href === current ? "active" : ""}
              aria-current={item.href === current ? "page" : undefined}
            >
              {item.label}
            </a>
          </li>
        ))}
      </ul>
      <span className="staff-who">
        <span className="staff-role-pill">{roleLabel(who.role)}</span>
        <span className="staff-email">{who.email || who.external_id}</span>
        <button
          type="button"
          className="staff-signout"
          onClick={() => {
            clearAuthToken();
            window.location.assign("/signin");
          }}
        >
          Sign out
        </button>
      </span>
    </nav>
  );
}

export default function StaffGuard({
  children,
  current,
  requireRoles,
}: {
  children: (who: StaffIdentity) => React.ReactNode;
  current: string;
  /** Narrow further than "staff" — e.g. the overview is admin/auditor only. */
  requireRoles?: string[];
}) {
  // Read the token as an external store rather than copying it into state in a
  // mount effect: no cascading render, and a sign-out in another tab is picked up.
  const token = useSyncExternalStore(subscribeAuthToken, getAuthToken, getServerAuthToken);
  const [state, setState] = useState<State>({ kind: "checking" });

  useEffect(() => {
    // Nothing to ask the API about; the render below derives the anonymous state.
    if (!token) return;
    let cancelled = false;
    (async () => {
      try {
        const res = await fetch("/api/v1/me", { headers: authHeaders() });
        if (res.status === 503) {
          if (!cancelled)
            setState({
              kind: "unavailable",
              detail:
                "Staff authentication is not configured on this deployment. Set the OIDC variables or an operator key.",
            });
          return;
        }
        const who = (await res.json()) as StaffIdentity;
        if (cancelled) return;
        setState(who?.authenticated ? { kind: "identified", who } : { kind: "anonymous" });
      } catch (err) {
        if (!cancelled)
          setState({ kind: "unavailable", detail: `Could not reach the backend: ${(err as Error).message}` });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [token]);

  if (!token || state.kind === "anonymous") {
    return <AccessGate kind="anonymous" requireRoles={requireRoles} />;
  }

  if (state.kind === "unavailable") {
    return <AccessGate kind="unavailable" detail={state.detail} requireRoles={requireRoles} />;
  }

  if (state.kind === "checking") {
    return (
      <main className="staff-gate">
        <p className="staff-gate-msg" role="status">
          Checking your access…
        </p>
      </main>
    );
  }

  // Identity is known; authorisation is decided here rather than in the effect.
  const allowed = requireRoles?.length
    ? requireRoles.includes(state.who.role)
    : isStaffRole(state.who.role);

  if (!allowed) {
    return <AccessGate kind="forbidden" who={state.who} requireRoles={requireRoles} />;
  }

  return (
    <>
      <StaffNav who={state.who} current={current} />
      {children(state.who)}
    </>
  );
}

/** The refusal / unavailable card. Extracted so the no-token path can render it
 *  without going through state. */
function AccessGate({
  kind,
  who,
  detail,
  requireRoles,
}: {
  kind: "anonymous" | "forbidden" | "unavailable";
  who?: StaffIdentity;
  detail?: string;
  requireRoles?: string[];
}) {
  const title =
    kind === "anonymous"
      ? "Sign in to continue"
      : kind === "forbidden"
        ? "You do not have access to this page"
        : "Staff tools unavailable";

  let body: string;
  if (kind === "anonymous") {
    body =
      "These pages show taxpayer escalations and operational data, so they need a staff sign-in.";
  } else if (kind === "forbidden" && who) {
    body = `You are signed in as ${who.email || who.external_id} with the role "${who.role}". ${
      requireRoles?.length
        ? `This page is limited to ${requireRoles.join(", ")}.`
        : "Staff access is required."
    }`;
  } else {
    body = detail || "Staff access is required.";
  }

  return (
    <main className="staff-gate">
      <div className="staff-gate-card">
        <h1>{title}</h1>
        <p>{body}</p>
        <div className="staff-gate-actions">
          <Link className="staff-gate-primary" href="/signin">
            Go to sign-in
          </Link>
          <Link className="staff-gate-link" href="/">
            Back to the assistant
          </Link>
        </div>
      </div>
    </main>
  );
}
