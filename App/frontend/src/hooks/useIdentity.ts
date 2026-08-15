"use client";

/**
 * Who is signed in, as one hook.
 *
 * Two facts have to agree before anything is shown as "signed in": a token
 * exists in this browser, and the backend accepts it. The token store answers
 * the first (localStorage, read through `useSyncExternalStore` so a sign-out in
 * another tab lands here too); `/v1/me` answers the second. Keeping them apart
 * is what lets the UI distinguish "not signed in" from "signed in with a token
 * this deployment rejects" — the second needs a different message, not a
 * silently empty panel.
 */

import { useCallback, useMemo, useSyncExternalStore } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  clearAuthToken,
  getAuthToken,
  getServerAuthToken,
  subscribeAuthToken,
} from "../lib/authSession";
import { isStaffRole, roleLabel } from "../lib/roles";
import { accountApi, ApiError, type Identity } from "../services/accountApi";

export type IdentityStatus =
  /** No token in this browser. */
  | "anonymous"
  /** Token present, `/v1/me` still in flight. */
  | "checking"
  /** Token accepted; `identity` is populated. */
  | "signed-in"
  /**
   * Token present and refused: `/v1/me` answered 401 (expired, wrong issuer,
   * wrong secret). This is a 401, NOT a 200 carrying `authenticated: false` —
   * `_resolve_bearer_context` in auth/dependencies.py raises HTTPException(401)
   * for any token that fails verification, and the `authenticated: false` body
   * is only ever returned when no Authorization header was sent at all. Reading
   * that wrong is what made this state unreachable in the first cut, so an
   * expired session reported itself as "the backend is down".
   */
  | "rejected"
  /** The backend could not be reached, or auth is not configured (503). */
  | "unavailable";

export interface IdentityState {
  status: IdentityStatus;
  identity: Identity | undefined;
  hasToken: boolean;
  isStaff: boolean;
  /** Best available name: display email, else the provider's subject id. */
  name: string;
  roleName: string;
  /** One or two letters for the avatar chip. */
  initials: string;
  error: Error | null;
  refresh: () => void;
  signOut: () => void;
}

/** "joshua.m@ura.go.ug" → "JM"; falls back to the first letter, then "?". */
function initialsFrom(name: string): string {
  const local = name.includes("@") ? name.slice(0, name.indexOf("@")) : name;
  const parts = local.split(/[.\-_\s]+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

export function useIdentity(): IdentityState {
  const token = useSyncExternalStore(subscribeAuthToken, getAuthToken, getServerAuthToken);
  const queryClient = useQueryClient();

  const query = useQuery<Identity>({
    // The token is part of the key so switching accounts refetches instead of
    // serving the previous identity from cache. This cache is in-memory for the
    // life of the tab — it is never persisted, logged, or sent anywhere.
    queryKey: ["me", token],
    queryFn: accountApi.me,
    enabled: Boolean(token),
    staleTime: 60_000,
    gcTime: 5 * 60_000,
    // A rejected token is a settled answer, not a transient failure.
    retry: false,
  });

  const refresh = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: ["me"] });
  }, [queryClient]);

  const signOut = useCallback(() => {
    clearAuthToken();
    // Drop the identity and everything derived from it, so no panel keeps
    // rendering the previous person's data until its own staleTime expires.
    queryClient.removeQueries({ queryKey: ["me"] });
    queryClient.removeQueries({ queryKey: ["profile"] });
    queryClient.removeQueries({ queryKey: ["consents"] });
  }, [queryClient]);

  return useMemo(() => {
    const identity = query.data?.authenticated ? query.data : undefined;

    let status: IdentityStatus;
    if (!token) status = "anonymous";
    else if (query.error) {
      // A refused token is a settled answer about THIS token; anything else
      // (503 when auth is unconfigured, a network failure) is a statement about
      // the backend, and the two need opposite advice: sign in again vs wait.
      status =
        query.error instanceof ApiError && query.error.status === 401
          ? "rejected"
          : "unavailable";
    } else if (!query.data) status = "checking";
    else if (identity) status = "signed-in";
    // A 200 with `authenticated: false` means the request carried no usable
    // credential at all — treat it as refused rather than signed in.
    else status = "rejected";

    const name = identity?.email || identity?.external_id || "";

    return {
      status,
      identity,
      hasToken: Boolean(token),
      isStaff: isStaffRole(identity?.role),
      name,
      roleName: roleLabel(identity?.role),
      initials: name ? initialsFrom(name) : "?",
      error: (query.error as Error | null) ?? null,
      refresh,
      signOut,
    };
  }, [token, query.data, query.error, refresh, signOut]);
}
