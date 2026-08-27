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
  getAuthMethod,
  getAuthToken,
  getServerAuthToken,
  subscribeAuthToken,
} from "../lib/authSession";
import { endOidcSession } from "../lib/oidcFlow";
import { isStaffRole, signedInName, staffSectionsFor } from "../lib/roles";
import { useTicketStream, type LiveEscalation } from "../hooks/useTicketStream";
import AccountMenu from "./ops/AccountMenu";
import {
  CommandPalette,
  CommandPaletteTrigger,
  useCommandPalette,
} from "./ops/CommandPalette";
import {
  BeakerIcon,
  ChartIcon,
  ChevronsLeftIcon,
  ChevronsRightIcon,
  FlagIcon,
  GaugeIcon,
  InboxIcon,
  ListIcon,
  PanelLeftIcon,
  SendIcon,
  SlidersIcon,
} from "./ops/icons";
import SettingsDialog, { type SettingsTab } from "./settings/SettingsDialog";
import { useSidebarMode } from "../hooks/useSidebarMode";
import { TicketLiveBanner } from "./staff/TicketLiveBanner";
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
  const method = getAuthMethod();
  clearAuthToken();
  // The other half of signing out: end the session at the identity provider,
  // or the next sign-in is answered silently from its surviving cookie and
  // hands back the same account. See useIdentity's signOut for the full note.
  // The provider's logout already redirects to /signin (OIDC_POST_LOGOUT_PATH),
  // so this only lands us there when there was nothing to log out of; issuing
  // both would cancel the logout navigation with a same-tab assign.
  if (method === "dev" || !endOidcSession()) window.location.assign("/signin");
}

/**
 * Icon per staff destination, keyed by href.
 *
 * Lives here rather than on StaffDestination because lib/roles.ts is imported
 * by the command palette and by server-safe code; giving it JSX would drag
 * React into modules that only want the route table.
 */
const DESTINATION_ICON: Record<string, () => React.JSX.Element> = {
  "/admin": GaugeIcon,
  "/agent": InboxIcon,
  "/admin/tickets": ListIcon,
  "/admin/flags": FlagIcon,
  "/admin/overrides": SlidersIcon,
  "/admin/outbox": SendIcon,
  "/analytics": ChartIcon,
  "/analytics/evaluation": BeakerIcon,
};

/**
 * Settings sections the console offers.
 *
 * Voice and Tax profile are the taxpayer's, not the officer's: one controls
 * narration of an answer this surface never renders, the other is a taxpayer's
 * own TIN and filing details. What is left is what an officer at a console
 * actually changes — appearance, response language, stored data, the account.
 */
const CONSOLE_SETTINGS_TABS: readonly SettingsTab[] = ["general", "privacy", "account"];

/**
 * The operations sidebar.
 *
 * Was a horizontal bar. It became a rail because the console outgrew it: eight
 * destinations in three groups scrolled sideways below 1080px, which hid the
 * group structure exactly where screen space made it most useful. A vertical
 * rail shows all three sections at once at every width.
 *
 * Collapsed it is 52px of icons; expanded, 208px with labels and section
 * headings. `hover` mode expands on pointer-over and collapses on leave;
 * `always-open` pins it. Both widths are driven by a CSS custom property on
 * the shell so the main content's offset stays in lockstep with the rail
 * instead of being duplicated as a magic number in two stylesheets.
 *
 * On mobile there is no rail: it becomes an off-canvas drawer behind a menu
 * button, since 52px of permanent chrome on a phone is most of the gutter.
 */
export function StaffNav({
  who,
  current,
  connected,
  onOpenPalette,
  onOpenSettings,
}: {
  who: StaffIdentity;
  current: string;
  /** Live-escalation socket state — a dot, not a full-width strip. */
  connected?: boolean;
  onOpenPalette?: () => void;
  onOpenSettings?: () => void;
}) {
  const sections = staffSectionsFor(who.role);
  const { mode, setMode } = useSidebarMode();
  const [hovered, setHovered] = useState(false);
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);

  // `accountOpen` holds the rail open the same way the pointer does. Without it
  // the account menu closes itself the moment the pointer leaves the 52px
  // column on its way to the menu — and `.staff-rail` is `overflow: hidden`,
  // so it would be clipped to a sliver on the way out.
  const expanded = mode === "always-open" || (mode === "hover" && (hovered || accountOpen));

  // Escape closes the drawer, matching the command palette's affordance.
  useEffect(() => {
    if (!drawerOpen) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setDrawerOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [drawerOpen]);

  const shellClass = ["staff-rail", expanded ? "is-expanded" : "is-collapsed", drawerOpen ? "is-drawer-open" : ""]
    .filter(Boolean)
    .join(" ");

  return (
    <>
      <a className="staff-skip" href="#staff-main">
        Skip to content
      </a>

      {/* Phone-only. The rail is display:none below the breakpoint, so without
          this there is no way to reach any destination. */}
      <button
        type="button"
        className="staff-rail-toggle"
        aria-expanded={drawerOpen}
        aria-controls="staff-rail"
        onClick={() => setDrawerOpen((open) => !open)}
      >
        <PanelLeftIcon />
        <span className="ops-sr-only">{drawerOpen ? "Close navigation" : "Open navigation"}</span>
      </button>

      {/* Click-catcher for the drawer. aria-hidden because the drawer itself
          is the thing screen readers should be in. */}
      <div
        className="staff-rail-scrim"
        aria-hidden="true"
        onClick={() => setDrawerOpen(false)}
      />

      <nav
        id="staff-rail"
        className={shellClass}
        aria-label="Operations"
        onMouseEnter={() => mode === "hover" && setHovered(true)}
        onMouseLeave={() => mode === "hover" && setHovered(false)}
      >
        <div className="staff-rail-head">
          <Link className="staff-brand" href="/admin" aria-label="URA Operations console">
            <span className="staff-brand-mark" aria-hidden="true">
              URA
            </span>
            <span className="staff-brand-text">
              Operations
              <span className="staff-brand-sub">Taxpayer assistant</span>
            </span>
          </Link>

          {/* Was a pin in the footer toolbar, three groups of links away from
              the thing it resizes. It belongs at the top-right corner of the
              panel it controls, and an arrow pointing the way the rail will
              move says what it does without asking anyone to read a pin as
              "stay open". */}
          <button
            type="button"
            className={`ops-icon-btn staff-rail-pin${mode === "always-open" ? " is-on" : ""}`}
            onClick={() => setMode(mode === "always-open" ? "hover" : "always-open")}
            aria-pressed={mode === "always-open"}
            title={mode === "always-open" ? "Collapse the sidebar" : "Keep the sidebar open"}
          >
            {mode === "always-open" ? <ChevronsLeftIcon /> : <ChevronsRightIcon />}
            <span className="ops-sr-only">
              {mode === "always-open" ? "Collapse the sidebar" : "Keep the sidebar open"}
            </span>
          </button>
        </div>

        {/* Directly under the brand, where every sidebar-and-search layout puts
            it — it was at the bottom of the rail, below eight destinations, in
            the one place nobody looks for a search field. */}
        {onOpenPalette ? (
          <div className="staff-rail-search">
            <CommandPaletteTrigger onOpen={onOpenPalette} />
          </div>
        ) : null}

        <div className="staff-rail-scroll">
          {sections.map((section) => (
            <div className="staff-rail-group" key={section.group}>
              {/* Hidden from sight when collapsed, never from the a11y tree —
                  the grouping is the whole point of the hierarchy. */}
              <p className="staff-rail-group-label" id={`rail-group-${section.group}`}>
                {section.label}
              </p>
              <ul aria-labelledby={`rail-group-${section.group}`}>
                {section.items.map((item) => {
                  const active = item.href === current;
                  const Icon = DESTINATION_ICON[item.href] ?? ListIcon;
                  return (
                    <li key={item.href}>
                      <a
                        href={item.href}
                        className={active ? "active" : ""}
                        aria-current={active ? "page" : undefined}
                        title={item.navLabel}
                        // Closes here rather than in a route-change effect:
                        // tapping a destination on a phone otherwise navigates
                        // behind a drawer that stays open over the new page.
                        onClick={() => setDrawerOpen(false)}
                      >
                        <span className="staff-rail-icon" aria-hidden="true">
                          <Icon />
                        </span>
                        <span className="staff-rail-label">{item.navLabel}</span>
                      </a>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </div>

        {/* One row — initials, name, account type, live dot, chevron — in place
            of a toolbar, a role pill, the raw address and a bare "Sign out"
            stacked four deep. All of it still exists, inside the menu the row
            opens; the theme control moved in there too. */}
        <div className="staff-rail-foot">
          <AccountMenu
            who={who}
            open={accountOpen}
            onOpenChange={setAccountOpen}
            onOpenSettings={() => onOpenSettings?.()}
            onSignOut={signOut}
            connected={connected}
          />
        </div>
      </nav>
    </>
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
  // The console's Settings is the app's Settings — theme, response language,
  // privacy and the account itself are the same preferences whichever surface
  // you reach them from, and a second dialog would be a second copy of them.
  // Voice and Tax profile are left out: neither has a reader in an operations
  // console (see SettingsDialog's `tabs`).
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [settingsTab, setSettingsTab] = useState<SettingsTab>("general");

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
        <div className="staff-gate-checking" aria-busy="true">
          <span className="staff-gate-spinner" aria-hidden="true" />
          <p className="staff-gate-msg" role="status">
            Checking your access…
          </p>
        </div>
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
    <div className="staff-shell">
      <StaffNav
        who={state.who}
        current={current}
        connected={live.connected}
        onOpenPalette={() => palette.setOpen(true)}
        onOpenSettings={() => {
          setSettingsTab("general");
          setSettingsOpen(true);
        }}
      />
      {/* The rail is fixed, so everything else lives in a column that is
          offset by the rail's current width. */}
      <div className="staff-shell-content">
        <TicketLiveBanner latest={live.latest as LiveEscalation | null} />
        {children(state.who)}
      </div>
      <CommandPalette
        role={state.who.role}
        open={palette.open}
        onClose={() => palette.setOpen(false)}
        onSignOut={signOut}
      />
      {/* Inside a `.chatv2` scope because that is where the dialog's tokens and
          styles live — the class is a token scope, not a layout, so a wrapper
          holding nothing but a fixed-position overlay changes no geometry. */}
      <div className="chatv2">
        <SettingsDialog
          open={settingsOpen}
          onClose={() => setSettingsOpen(false)}
          tab={settingsTab}
          onTabChange={setSettingsTab}
          tabs={CONSOLE_SETTINGS_TABS}
        />
      </div>
    </div>
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
    body = `You are signed in as ${signedInName(who)} with the role "${who.role}". ${
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
