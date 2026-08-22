/**
 * Role vocabulary — one copy of "which roles are staff" for the whole frontend.
 *
 * Mirrors `AuthUser.role` / `AuthUser.is_staff` in
 * App/backend/app/auth/models.py. The same three-role list had been inlined in
 * the sign-in page, the OIDC callback and the staff guard; a fourth copy for
 * the sidebar account block would have been one too many.
 *
 * This is presentation only. The gate that matters is `require_admin_access`
 * on the API — nothing here grants access to anything.
 */

export const STAFF_ROLES = ["ura_staff", "ura_admin", "ura_auditor"] as const;

const STAFF_ROLE_SET: ReadonlySet<string> = new Set(STAFF_ROLES);

export function isStaffRole(role: string | undefined | null): boolean {
  return Boolean(role && STAFF_ROLE_SET.has(role));
}

/** Human labels for every role the backend can put in a token. */
export const ROLE_LABEL: Record<string, string> = {
  public: "Visitor",
  verified_taxpayer: "Taxpayer",
  ura_staff: "Tax agent",
  ura_admin: "Administrator",
  ura_auditor: "Auditor",
};

export function roleLabel(role: string | undefined | null): string {
  if (!role) return "Visitor";
  return ROLE_LABEL[role] ?? role;
}

/** What a staff sign-in should land on: agents work the queue, the rest oversee. */
export function staffLandingPath(role: string | undefined | null): string {
  return role === "ura_staff" ? "/agent" : "/admin";
}

/**
 * Operations pages a role may open.
 *
 * One list, three consumers: the console's own nav bar, the command palette,
 * and the account menu. `navLabel` is the short form the nav bar and palette
 * show; `label` stays the long form the account menu has always used, so the
 * existing callers keep rendering exactly what they rendered before.
 *
 * `group` is what turns seven sibling links into a hierarchy: an administrator
 * sees three sections rather than a flat row, which is the difference between
 * scanning and reading.
 */
export type StaffSection = "work" | "configure" | "observe";

export const SECTION_LABEL: Record<StaffSection, string> = {
  work: "Work",
  configure: "Configure",
  observe: "Observe",
};

export interface StaffDestination {
  href: string;
  /** Long form — account menu and sign-in landing card. */
  label: string;
  /** Short form — console nav and command palette. */
  navLabel: string;
  group: StaffSection;
  /** One line of "what is this page for", shown in the command palette. */
  blurb: string;
  roles: readonly string[];
}

export const STAFF_DESTINATIONS: readonly StaffDestination[] = [
  {
    href: "/admin",
    label: "Operations overview",
    navLabel: "Overview",
    group: "work",
    blurb: "SLA, escalations waiting longest, answer authority",
    roles: ["ura_admin", "ura_auditor"],
  },
  {
    href: "/agent",
    label: "My queue",
    navLabel: "My queue",
    group: "work",
    blurb: "Claim a case and reply, one at a time",
    roles: ["ura_staff", "ura_admin"],
  },
  {
    href: "/admin/tickets",
    label: "All tickets",
    navLabel: "All tickets",
    group: "work",
    blurb: "Every escalation, every status, every team",
    roles: STAFF_ROLES,
  },
  {
    href: "/admin/flags",
    label: "Flags",
    navLabel: "Flags",
    group: "configure",
    blurb: "What this replica is serving right now",
    roles: ["ura_admin", "ura_auditor"],
  },
  {
    href: "/admin/overrides",
    label: "Answer overrides",
    navLabel: "Overrides",
    group: "configure",
    blurb: "Exact-match staff replies for known questions",
    roles: ["ura_admin", "ura_auditor"],
  },
  {
    href: "/admin/outbox",
    label: "Notification outbox",
    navLabel: "Outbox",
    group: "configure",
    blurb: "Queued email and SMS rows",
    roles: ["ura_admin", "ura_auditor"],
  },
  {
    href: "/analytics",
    label: "Analytics",
    navLabel: "Analytics",
    group: "observe",
    blurb: "Service levels, topics, retrieval and satisfaction",
    roles: ["ura_admin", "ura_auditor"],
  },
  {
    href: "/analytics/evaluation",
    label: "Answer evaluation",
    navLabel: "Evaluation",
    group: "observe",
    blurb: "RAG quality metrics against their thresholds",
    roles: ["ura_admin", "ura_auditor"],
  },
];

export function staffDestinationsFor(role: string | undefined | null): StaffDestination[] {
  if (!role) return [];
  return STAFF_DESTINATIONS.filter((d) => d.roles.includes(role));
}

/** The same destinations, bucketed for a nav that shows sections. */
export function staffSectionsFor(
  role: string | undefined | null,
): { group: StaffSection; label: string; items: StaffDestination[] }[] {
  const allowed = staffDestinationsFor(role);
  return (["work", "configure", "observe"] as StaffSection[])
    .map((group) => ({
      group,
      label: SECTION_LABEL[group],
      items: allowed.filter((d) => d.group === group),
    }))
    .filter((section) => section.items.length > 0);
}
