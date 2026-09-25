'use client';

import React, { useCallback, useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import type { StaffIdentity } from '@/components/StaffGuard';
import { isTypingTarget } from '@/lib/ticketUi';
import { acquireLobby } from '@/services/callLobbySocket';
import { toggleHold, toggleMute } from '@/services/officerCallSession';
import { useCallConsoleStore } from '@/store/useCallConsoleStore';
import { ActiveCallDock } from './ActiveCallDock';
import { CallAlertStack } from './CallAlertStack';
import { CallConsoleBar } from './CallConsoleBar';
import { DeviceCheckDialog } from './DeviceCheckDialog';
import { usePresenceHeartbeat } from './usePresenceHeartbeat';
import './callConsole.css';

/** Roles that take calls; auditors watch. */
const CALL_TAKERS = new Set(['ura_staff', 'ura_admin']);

/**
 * The console's call layer (plan §8.2): the call bar, transfer alerts, the
 * active-call dock and the device check, on every staff page. Mounted by
 * `StaffGuard`; the state it shows lives outside React, so navigating between
 * pages neither drops the officer's call nor reopens the lobby socket.
 */
export function StaffCallLayer({ role, who }: { role: string; who?: StaffIdentity }) {
  const router = useRouter();
  const lobbyStatus = useCallConsoleStore((s) => s.lobbyStatus);
  const requestDesk = useCallConsoleStore((s) => s.requestDesk);
  const [deviceOpen, setDeviceOpen] = useState(false);
  const canTake = CALL_TAKERS.has(role);

  useEffect(() => acquireLobby(), []);
  usePresenceHeartbeat(canTake ? who?.external_id || who?.email || 'staff' : undefined);

  const openCall = useCallback(
    (callId: string) => {
      requestDesk(callId);
      router.push(`/calls?call=${encodeURIComponent(callId)}`);
    },
    [requestDesk, router],
  );

  // M, H, and E work on every page while the officer is on a call.
  useEffect(() => {
    if (!canTake) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.metaKey || event.ctrlKey || event.altKey || isTypingTarget(event.target)) return;
      const { activeCall, setConfirmEnd } = useCallConsoleStore.getState();
      if (activeCall?.state !== 'bridged') return;
      if (event.key === 'm' || event.key === 'M') {
        event.preventDefault();
        toggleMute();
      } else if (event.key === 'h' || event.key === 'H') {
        event.preventDefault();
        toggleHold();
      } else if (event.key === 'e' || event.key === 'E') {
        event.preventDefault();
        setConfirmEnd(true);
      } else if (event.key === 'Escape') {
        setConfirmEnd(false);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [canTake]);

  if (lobbyStatus === 'disabled') return null;

  return (
    <>
      <CallConsoleBar canTake={canTake} onOpenCall={openCall} onCheckMic={() => setDeviceOpen(true)} />
      <CallAlertStack canTake={canTake} onPreview={openCall} />
      {canTake && <ActiveCallDock onBackToCall={openCall} />}
      <DeviceCheckDialog open={deviceOpen} onClose={() => setDeviceOpen(false)} />
    </>
  );
}
