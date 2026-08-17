"use client";

import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { appendAuthToken } from "../lib/authSession";

export interface LiveEscalation {
  id: string;
  priority: string;
  reason: string;
}

/**
 * Subscribe to `/v1/admin/tickets/stream`. The socket is read-only and
 * carries triage metadata only. On `escalation.created` the queue, SLA
 * and stats queries are invalidated so the officer does not wait for poll.
 */
export function useTicketStream(enabled: boolean): {
  latest: LiveEscalation | null;
  connected: boolean;
} {
  const client = useQueryClient();
  const [latest, setLatest] = useState<LiveEscalation | null>(null);
  const [connected, setConnected] = useState(false);
  const latestRef = useRef(latest);
  latestRef.current = latest;

  useEffect(() => {
    if (!enabled || typeof window === "undefined") return;
    let closed = false;
    let socket: WebSocket | null = null;
    let retry: number | undefined;

    const connect = () => {
      if (closed) return;
      const proto = window.location.protocol === "https:" ? "wss" : "ws";
      const url = appendAuthToken(
        `${proto}://${window.location.host}/api/v1/admin/tickets/stream`,
      );
      socket = new WebSocket(url);
      socket.onopen = () => setConnected(true);
      socket.onclose = () => {
        setConnected(false);
        if (!closed) retry = window.setTimeout(connect, 4000);
      };
      socket.onerror = () => socket?.close();
      socket.onmessage = (event) => {
        try {
          const data = JSON.parse(String(event.data)) as {
            type?: string;
            event?: string;
            id?: string;
            priority?: string;
            reason?: string;
          };
          const kind = data.type || data.event;
          if (kind !== "escalation.created" || !data.id) return;
          setLatest({
            id: data.id,
            priority: data.priority || "normal",
            reason: data.reason || "New escalation",
          });
          client.invalidateQueries({ queryKey: ["ticketQueueFull"] });
          client.invalidateQueries({ queryKey: ["ticketSla"] });
          client.invalidateQueries({ queryKey: ["ticketStats"] });
        } catch {
          /* keepalive / malformed */
        }
      };
    };

    connect();
    return () => {
      closed = true;
      if (retry) window.clearTimeout(retry);
      socket?.close();
    };
  }, [enabled, client]);

  return { latest, connected };
}
