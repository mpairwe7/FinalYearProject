/** One thing a service can look up for the taxpayer. */
export interface ChatConnectorOperation {
  name: string;
  title: string;
  description: string;
}

/** Browser-safe discovery shape for reviewed, deployment-configured MCP services. */
export interface ChatConnector {
  namespace: string;
  label: string;
  description: string;
  operation_count: number;
  read_only: true;
  /*
   * Reported by servers that say what is behind a service. All optional: an
   * older API sends none of them, and the interface then claims nothing it was
   * not told (no "Demo data" badge, no availability).
   */
  /** The description without the demo-data notice that `description` carries. */
  summary?: string;
  /** `simulation` is sample data, never a live URA record. */
  mode?: 'simulation' | 'live';
  /** `unavailable`: the service is bound but is not answering right now. */
  status?: 'available' | 'unavailable';
  operations?: ChatConnectorOperation[];
  /** Which server software answered. Sent to staff roles only. */
  server?: { name?: string; version?: string };
}

/** The two letters shown on a service's tile: initials for a name of several words, else its first two letters. */
export function connectorMonogram(label: string): string {
  const words = label.split(/\s+/).filter(Boolean);
  const letters =
    words.length > 1 ? words.slice(0, 2).map((word) => word[0]).join('') : (words[0] ?? '').slice(0, 2);
  return (letters || label.slice(0, 2)).toUpperCase();
}
