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

/** Operations pages a role may open — drives the account menu's staff links. */
export const STAFF_DESTINATIONS: readonly {
  href: string;
  label: string;
  roles: readonly string[];
}[] = [
  { href: "/admin", label: "Operations overview", roles: ["ura_admin", "ura_auditor"] },
  { href: "/agent", label: "My queue", roles: ["ura_staff", "ura_admin"] },
  { href: "/admin/tickets", label: "All tickets", roles: STAFF_ROLES },
  { href: "/admin/flags", label: "Flags", roles: ["ura_admin", "ura_auditor"] },
  { href: "/admin/overrides", label: "Answer overrides", roles: ["ura_admin", "ura_auditor"] },
  { href: "/admin/outbox", label: "Notification outbox", roles: ["ura_admin", "ura_auditor"] },
  { href: "/analytics", label: "Analytics", roles: ["ura_admin", "ura_auditor"] },
];

export function staffDestinationsFor(role: string | undefined | null) {
  if (!role) return [];
  return STAFF_DESTINATIONS.filter((d) => d.roles.includes(role));
}
