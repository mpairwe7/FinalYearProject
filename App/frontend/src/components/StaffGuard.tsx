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
import React, { useEffect, useState } from "react";
import { authHeaders, clearAuthToken, getAuthToken } from "../lib/authSession";
import "./staffGuard.css";

/** Mirrors `AuthUser.is_staff` in App/backend/app/auth/models.py. */
const STAFF_ROLES = new Set(["ura_staff", "ura_admin", "ura_auditor"]);

const ROLE_LABEL: Record<string, string> = {
  ura_staff: "Tax agent",
  ura_admin: "Administrator",
  ura_auditor: "Auditor",
};

export interface StaffIdentity {
  authenticated: boolean;
  role: string;
  email?: string;
  external_id?: string;
  tenant_id?: string;
}

type State =
  | { kind: "checking" }
  | { kind: "anonymous" }
  | { kind: "forbidden"; who: StaffIdentity }
  | { kind: "unavailable"; detail: string }
  | { kind: "ok"; who: StaffIdentity };

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
        <span className="staff-role-pill">{ROLE_LABEL[who.role] || who.role}</span>
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
  const [state, setState] = useState<State>({ kind: "checking" });

  useEffect(() => {
    let cancelled = false;
    (async () => {
      if (!getAuthToken()) {
        if (!cancelled) setState({ kind: "anonymous" });
        return;
      }
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
        if (!who?.authenticated) {
          setState({ kind: "anonymous" });
          return;
        }
        const allowed = requireRoles?.length
          ? requireRoles.includes(who.role)
          : STAFF_ROLES.has(who.role);
        setState(allowed ? { kind: "ok", who } : { kind: "forbidden", who });
      } catch (err) {
        if (!cancelled)
          setState({ kind: "unavailable", detail: `Could not reach the backend: ${(err as Error).message}` });
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [requireRoles]);

  if (state.kind === "checking") {
    return (
      <main className="staff-gate">
        <p className="staff-gate-msg" role="status">
          Checking your access…
        </p>
      </main>
    );
  }

  if (state.kind === "ok") {
    return (
      <>
        <StaffNav who={state.who} current={current} />
        {children(state.who)}
      </>
    );
  }

  const title =
    state.kind === "anonymous"
      ? "Sign in to continue"
      : state.kind === "forbidden"
        ? "You do not have access to this page"
        : "Staff tools unavailable";

  const body =
    state.kind === "anonymous"
      ? "These pages show taxpayer escalations and operational data, so they need a staff sign-in."
      : state.kind === "forbidden"
        ? `You are signed in as ${state.who.email || state.who.external_id} with the role "${state.who.role}". ${
            requireRoles?.length
              ? `This page is limited to ${requireRoles.join(", ")}.`
              : "Staff access is required."
          }`
        : state.detail;

  return (
    <main className="staff-gate">
      <div className="staff-gate-card">
        <h1>{title}</h1>
        <p>{body}</p>
        <div className="staff-gate-actions">
          <a className="staff-gate-primary" href="/signin">
            Go to sign-in
          </a>
          <a className="staff-gate-link" href="/">
            Back to the assistant
          </a>
        </div>
      </div>
    </main>
  );
}
