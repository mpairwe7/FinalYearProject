import { useSyncExternalStore } from 'react';
import { getAuthSubject, subscribeAuthToken } from '@/lib/authSession';

/**
 * The signed-in officer's account id, for telling "passed to you" from
 * "reserved for a colleague". Display only: the server refuses a claim on a
 * call reserved for someone else whatever this says.
 */
export function useMyUserId(): string {
  return useSyncExternalStore(subscribeAuthToken, getAuthSubject, () => '');
}
