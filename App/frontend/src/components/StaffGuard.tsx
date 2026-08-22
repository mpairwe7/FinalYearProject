"use client";

/**
 * Client-side staff gate + the operations console shell.
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
 *
 * The shell around it owns three things the pages used to do badly or not at
 * all: navigation with a hierarchy (Work / Configure / Observe rather than
 * eight sibling links), a theme control on staff pages (there was none — only
 * /analytics had one, from a nav that no longer exists), and a single live
 * escalation subscription shared by the nav dot and the arrivals banner instead
 * of one socket per component that wants to know.
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
import { isStaffRole, roleLabel, staffSectionsFor } from "../lib/roles";
import { useTicketStream, type LiveEscalation } from "../hooks/useTicketStream";
import {
  CommandPalette,
  CommandPaletteTrigger,
  useCommandPalette,
} from "./ops/CommandPalette";
import { TicketLiveBanner } from "./staff/TicketLiveBanner";
import ThemeToggle from "./ThemeToggle";
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

function signOut() {
  clearAuthToken();
  window.location.assign("/signin");
}

export function StaffNav({
  who,
  current,
  connected,
  onOpenPalette,
}: {
  who: StaffIdentity;
  current: string;
  /** Live-escalation socket state — a dot, not a full-width strip. */
  connected?: boolean;
  onOpenPalette?: () => void;
}) {
  const sections = staffSectionsFor(who.role);

  return (
    <nav className="staff-nav" aria-label="Operations">
      <a className="staff-skip" href="#staff-main">
        Skip to content
      </a>

      {/* Named explicitly: below 1080px `.staff-brand-text` is display:none and
          the mark is decorative, which left this link with no accessible name
          at all on a phone — a failure only the mobile axe project could see. */}
      <Link className="staff-brand" href="/admin" aria-label="URA Operations console">
        <span className="staff-brand-mark" aria-hidden="true">
          URA
        </span>
        <span className="staff-brand-text">
          Operations
          <span className="staff-brand-sub">Taxpayer assistant</span>
        </span>
      </Link>

      {/* Below 1080px this strip scrolls inside the bar rather than wrapping
          the whole nav to a second and third row, which is what pushed the
          console's content below the fold on a laptop. */}
      <div className="staff-nav-scroller">
        <ul>
          {sections.map((section, index) => (
            <React.Fragment key={section.group}>
              {index > 0 ? <li className="staff-nav-sep" aria-hidden="true" /> : null}
              {section.items.map((item) => {
                const active = item.href === current;
                return (
                  <li key={item.href}>
                    <a
                      href={item.href}
                      className={active ? "active" : ""}
                      aria-current={active ? "page" : undefined}
                    >
                      {item.navLabel}
                    </a>
                  </li>
                );
              })}
            </React.Fragment>
          ))}
        </ul>
      </div>

      <div className="staff-nav-end">
        {onOpenPalette ? <CommandPaletteTrigger onOpen={onOpenPalette} /> : null}
        <span
          className={`staff-live-dot${connected ? " is-on" : ""}`}
          title={connected ? "Live escalations connected" : "Reconnecting to the escalation stream"}
          role="status"
        >
          <span className="ops-sr-only">
            {connected ? "Live escalations connected" : "Reconnecting to the escalation stream"}
          </span>
        </span>
        <ThemeToggle className="ops-icon-btn" />
        <span className="staff-who">
          <span className="staff-role-pill">{roleLabel(who.role)}</span>
          <span className="staff-email" title={who.email || who.external_id}>
            {who.email || who.external_id}
          </span>
          <button type="button" className="staff-signout" onClick={signOut}>
            Sign out
          </button>
        </span>
      </div>
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
  const palette = useCommandPalette();

  // One socket for the whole console. TicketLiveBanner used to open its own on
  // every staff route; the nav dot would have opened a second.
  const live = useTicketStream(state.kind === "identified");

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
      <StaffNav
        who={state.who}
        current={current}
        connected={live.connected}
        onOpenPalette={() => palette.setOpen(true)}
      />
      <TicketLiveBanner latest={live.latest as LiveEscalation | null} />
      {children(state.who)}
      <CommandPalette
        role={state.who.role}
        open={palette.open}
        onClose={() => palette.setOpen(false)}
        onSignOut={signOut}
      />
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
        <span className="staff-gate-mark" aria-hidden="true">
          URA
        </span>
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
