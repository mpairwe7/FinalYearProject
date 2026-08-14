/**
 * Account API client — the `/v1/me` family.
 *
 * These are the only endpoints that read or write anything about the signed-in
 * person: identity (`/v1/me`), the tax/personalization profile the answers are
 * shaped by (`/v1/me/profile`), consent receipts (`/v1/me/consents`), the UDPA
 * 2019 data-portability export (`/v1/me/export`) and the right-to-erasure call
 * (`DELETE /v1/me`). The settings surface is built on exactly this list — a
 * control that has no endpoint here has nothing to save to and is not offered.
 *
 * Every call goes through the same-origin `/api/*` rewrite, so no CORS and no
 * backend host in the client bundle.
 */

import { authHeaders } from "@/lib/authSession";

const BASE = "/api";
const TIMEOUT_MS = 15_000;

/**
 * Version tag recorded on each consent receipt. `voice_consent.py` writes
 * "1.0" for the voice purposes, so grants from this UI carry the same version
 * rather than inventing a second one the backend has never seen.
 */
export const CONSENT_VERSION = "1.0";

/** Mirrors `AuthUser.role` in App/backend/app/auth/models.py. */
export type UserRole =
  | "public"
  | "verified_taxpayer"
  | "ura_staff"
  | "ura_admin"
  | "ura_auditor";

/** Mirrors `ConsentPurpose` in App/backend/app/auth/models.py. */
export type ConsentPurpose =
  | "personalization"
  | "analytics"
  | "ticket_escalation"
  | "long_term_storage"
  | "ura_account_access"
  | "ura_actions"
  | "voice_recording"
  | "voice_analytics";

/** Mirrors `TaxpayerType` in App/backend/app/auth/models.py. */
export type TaxpayerType =
  | "unknown"
  | "individual"
  | "sole_trader"
  | "company"
  | "partnership"
  | "ngo"
  | "non_resident";

export type DetailLevel = "beginner" | "intermediate" | "expert";

/** Shape of `GET /v1/me` — anonymous callers get `authenticated: false`. */
export interface Identity {
  authenticated: boolean;
  role: UserRole | string;
  tenant_id?: string;
  user_id?: string;
  external_id?: string;
  email?: string;
  granted_purposes?: string[];
}

export interface UserProfile {
  user_id: string;
  taxpayer_type: TaxpayerType;
  industry: string;
  /** The backend profile only stores en/lg; the chat locale set is wider. */
  primary_language: "en" | "lg";
  detail_level: DetailLevel;
  registered_tax_types: string[];
  fiscal_year: string;
  display_name: string;
  updated_at: number;
}

/** PUT payload — only the keys present are written (`ProfileUpdateRequest`). */
export type ProfilePatch = Partial<
  Pick<
    UserProfile,
    | "taxpayer_type"
    | "industry"
    | "primary_language"
    | "detail_level"
    | "registered_tax_types"
    | "display_name"
  >
>;

export interface ConsentReceipt {
  receipt_id: string;
  user_id: string;
  purpose: ConsentPurpose | string;
  version: string;
  granted_at: number;
  withdrawn_at: number | null;
  legal_basis: string;
}

/** An HTTP failure that keeps its status, so callers can tell 401 from 503. */
export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request<T>(
  path: string,
  init: RequestInit & { json?: unknown } = {},
): Promise<T> {
  const { json, headers, ...rest } = init;
  const res = await fetch(`${BASE}${path}`, {
    ...rest,
    headers: authHeaders({
      ...(json === undefined ? {} : { "Content-Type": "application/json" }),
      ...((headers as Record<string, string> | undefined) ?? {}),
    }),
    ...(json === undefined ? {} : { body: JSON.stringify(json) }),
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  if (!res.ok) {
    // FastAPI puts the useful part in `detail`; fall back to the status line so
    // an HTML error page does not surface as "undefined".
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(res.status, detail);
  }
  return res.json() as Promise<T>;
}

export const accountApi = {
  /** Identity + role. Safe to call anonymously: returns `authenticated: false`. */
  me: () => request<Identity>("/v1/me"),

  profile: () => request<UserProfile>("/v1/me/profile"),

  updateProfile: (patch: ProfilePatch) =>
    request<UserProfile>("/v1/me/profile", { method: "PUT", json: patch }),

  consents: () =>
    request<{ user_id: string; consents: ConsentReceipt[] }>("/v1/me/consents"),

  grantConsents: (purposes: ConsentPurpose[]) =>
    request<{ user_id: string; granted: unknown[] }>("/v1/me/consents/grant", {
      method: "POST",
      json: { purposes, version: CONSENT_VERSION },
    }),

  withdrawConsents: (purposes: ConsentPurpose[]) =>
    request<{ user_id: string; withdrawn: unknown[] }>("/v1/me/consents/withdraw", {
      method: "POST",
      json: { purposes },
    }),

  /** UDPA 2019 data-portability export: identity, profile, consents, chats. */
  export: () => request<Record<string, unknown>>("/v1/me/export"),

  /** UDPA 2019 right to erasure. Irreversible — confirm before calling. */
  erase: () =>
    request<{ deleted: Record<string, number>; external_id: string }>("/v1/me", {
      method: "DELETE",
    }),
};
