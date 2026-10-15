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

/** How a lookup in a service ended, as the server classified it. */
export type ConnectorOutcome = 'found' | 'no_record' | 'needs_input' | 'unavailable';

/** A service an answer consulted: shown beside the answer so the reader knows where it came from. */
export interface ConnectorUse {
  namespace: string;
  label: string;
  mode?: 'simulation' | 'live';
  outcome?: ConnectorOutcome;
}

/** Why a selected service was left out of a turn (`connector_notices` on the chat response). */
export type ConnectorSkipReason = 'disabled' | 'unavailable' | 'not_offered';

export interface ConnectorNotice {
  namespace: string;
  label: string;
  reason: ConnectorSkipReason;
}

/** At most this many services can be selected for one conversation (the API's limit). */
export const MAX_SELECTED_CONNECTORS = 8;

const NAMESPACE_RE = /^[a-z0-9_]{1,64}$/;
const OUTCOMES: readonly ConnectorOutcome[] = ['found', 'no_record', 'needs_input', 'unavailable'];
const SKIP_REASONS: readonly ConnectorSkipReason[] = ['disabled', 'unavailable', 'not_offered'];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

export function isConnectorNamespace(value: unknown): value is string {
  return typeof value === 'string' && NAMESPACE_RE.test(value);
}

/** The two letters shown on a service's tile: initials for a name of several words, else its first two letters. */
export function connectorMonogram(label: string): string {
  const words = label.split(/\s+/).filter(Boolean);
  const letters =
    words.length > 1 ? words.slice(0, 2).map((word) => word[0]).join('') : (words[0] ?? '').slice(0, 2);
  return (letters || label.slice(0, 2)).toUpperCase();
}

/** A readable name for a namespace the catalog has not described (yet): `payment_system` → "Payment System". */
export function fallbackConnectorLabel(namespace: string): string {
  return namespace
    .split('_')
    .filter(Boolean)
    .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
    .join(' ');
}

function cleanLabel(value: unknown, namespace: string): string {
  const text = typeof value === 'string' ? value.replace(/\s+/g, ' ').trim().slice(0, 80) : '';
  return text || fallbackConnectorLabel(namespace);
}

/**
 * The services an answer consulted, from the stream's `agent_trace` frame.
 *
 * One entry per service, in the order first asked. When a service was asked
 * more than once its last outcome wins: that is the one the reply reports.
 * Anything malformed is skipped — a trace is a display hint, never trusted.
 */
export function connectorsUsedFromTrace(trace: unknown): ConnectorUse[] {
  if (!Array.isArray(trace)) return [];
  const used = new Map<string, ConnectorUse>();
  for (const item of trace) {
    if (!isRecord(item) || item.type !== 'tool_call.completed') continue;
    const namespace = item.connector;
    if (!isConnectorNamespace(namespace)) continue;
    const entry: ConnectorUse = { namespace, label: cleanLabel(item.connector_label, namespace) };
    if (item.connector_mode === 'simulation' || item.connector_mode === 'live') entry.mode = item.connector_mode;
    if (OUTCOMES.includes(item.connector_outcome as ConnectorOutcome)) {
      entry.outcome = item.connector_outcome as ConnectorOutcome;
    }
    used.set(namespace, { ...used.get(namespace), ...entry });
  }
  return [...used.values()];
}

/** `connector_notices` from a chat response or metadata frame, validated. */
export function parseConnectorNotices(value: unknown): ConnectorNotice[] {
  if (!Array.isArray(value)) return [];
  const notices: ConnectorNotice[] = [];
  for (const item of value) {
    if (!isRecord(item) || !isConnectorNamespace(item.namespace)) continue;
    if (!SKIP_REASONS.includes(item.reason as ConnectorSkipReason)) continue;
    notices.push({
      namespace: item.namespace,
      label: cleanLabel(item.label, item.namespace),
      reason: item.reason as ConnectorSkipReason,
    });
  }
  return notices;
}

/** Stored uses and notices, validated again on the way back in from localStorage. */
export function sanitizeConnectorUses(value: unknown): ConnectorUse[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter(isRecord)
    .filter((item) => isConnectorNamespace(item.namespace))
    .map((item) => {
      const namespace = item.namespace as string;
      const use: ConnectorUse = { namespace, label: cleanLabel(item.label, namespace) };
      if (item.mode === 'simulation' || item.mode === 'live') use.mode = item.mode;
      if (OUTCOMES.includes(item.outcome as ConnectorOutcome)) use.outcome = item.outcome as ConnectorOutcome;
      return use;
    })
    .slice(0, MAX_SELECTED_CONNECTORS);
}

/** A conversation's stored selection: namespaces only, de-duplicated and bounded. */
export function sanitizeConnectorSelection(value: unknown): string[] {
  if (!Array.isArray(value)) return [];
  return [...new Set(value.filter(isConnectorNamespace))].slice(0, MAX_SELECTED_CONNECTORS);
}
