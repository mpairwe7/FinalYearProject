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

/**
 * A name an officer recognises as their own.
 *
 * The console rendered `who.email || who.external_id`, and on a deployment
 * where the identity provider returns no email that second branch shows the
 * raw subject — `/agent` greeted its user with
 * "Signed in as auth0|6a7d7af3ace1faccc70dc644". That is the system's
 * vocabulary, not the reader's, and it is not even identifying: an opaque hex
 * string tells an officer nothing about which account they are in.
 *
 * So: the email when there is one. A provider-prefixed subject
 * (`auth0|…`, `google-oauth2|…`) has no human part, so it becomes the role
 * name with a short tail — enough to tell two accounts apart, without pretending
 * the digits mean something. Anything else is passed through, because an
 * identity provider that returns a username should show the username.
 */
export function signedInName(
  who: { email?: string; external_id?: string; role?: string } | null | undefined,
): string {
  if (!who) return "your account";
  if (who.email) return who.email;

  const id = who.external_id ?? "";
  if (!id) return "your account";

  const opaque = /^[a-z0-9-]+\|(.+)$/i.exec(id);
  if (opaque) {
    const tail = opaque[1].slice(-6);
    return `${roleLabel(who.role)} · …${tail}`;
  }
  return id;
}

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

/* --------------------------------------------------------------------------
 * The console's account row
 *
 * The sidebar footer used to be three stacked pieces — a role pill, the raw
 * email, and a "Sign out" button — which is the whole account surface spelled
 * out in a 208px column. It is now one row that opens a menu, so it needs the
 * three short strings that row is made of: an avatar's letters, a name, and the
 * account type after a middle dot.
 * ------------------------------------------------------------------------ */

/** Two letters for the avatar, taken from the account type: there is no
 *  person's name in a token — only an email and a role — so initials of a name
 *  we do not have would be invented. `ura_admin` → "AD", `ura_staff` → "ST". */
export function roleInitials(role: string | undefined | null): string {
  switch (role) {
    case "ura_admin":
      return "AD";
    case "ura_staff":
      return "ST";
    case "ura_auditor":
      return "AU";
    default:
      return roleLabel(role).slice(0, 2).toUpperCase();
  }
}

/** The one-word account type shown after the dot — "Admin", not
 *  "Administrator", which does not fit beside a name in the rail. */
export function roleShortLabel(role: string | undefined | null): string {
  switch (role) {
    case "ura_admin":
      return "Admin";
    case "ura_staff":
      return "Staff";
    case "ura_auditor":
      return "Auditor";
    default:
      return roleLabel(role);
  }
}

/**
 * A first name for the account row.
 *
 * The closest thing to one an OIDC token carries is the local part of the
 * email: `officer.admin@ura.go.ug` → "Officer". Anything without a usable local
 * part (no email, or a provider-prefixed subject like `auth0|6a7d…`) returns an
 * empty string, and the row then shows the account type alone rather than
 * "Admin · Admin".
 */
export function accountDisplayName(
  who: { email?: string; external_id?: string } | null | undefined,
): string {
  const email = who?.email?.trim();
  if (!email) return "";
  const local = email.slice(0, email.indexOf("@") === -1 ? undefined : email.indexOf("@"));
  const first = local.split(/[.\-_+\s]+/).filter(Boolean)[0];
  if (!first || /^\d+$/.test(first)) return "";
  return first.charAt(0).toUpperCase() + first.slice(1);
}
