import type {
  ChatTurn,
  Citation,
  ContextResource,
  ObjectiveHypothesis,
  PortalCopilotPayload,
  WorkflowState,
} from '../store/useChatStore';
import type { ConnectorNotice } from './connectors';

/** A turn's metadata as the server sends it (SSE `metadata`/`grounding`/`done`, or a metadata line). */
export type TurnMeta = Record<string, unknown>;

/** Whether officers were on duty when a handoff was queued (`app.officer_hours.status`). */
export interface OfficerHours {
  onDuty: boolean;
  /** For display, e.g. "07:00–23:00 EAT". */
  hours: string;
  /** ISO 8601 start of the next shift, or null. */
  nextOnDuty: string | null;
}

/**
 * When officers are next on duty, for the reader: weekday, date and time in
 * their own time zone (with its abbreviation) and language — the next shift can
 * be days away, which the hours alone do not say. Null when unreadable.
 */
export function formatNextShift(iso: string, locale: string, timeZone?: string): string | null {
  const when = new Date(iso);
  if (Number.isNaN(when.getTime())) return null;
  try {
    return new Intl.DateTimeFormat([locale, 'en-UG'], {
      weekday: 'long',
      day: 'numeric',
      month: 'long',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
      timeZoneName: 'short',
      ...(timeZone ? { timeZone } : {}),
    }).format(when);
  } catch {
    return null;
  }
}

/**
 * The desk's hours from a frame: `officer_hours` on the handoff the server
 * attaches to every escalation (both chat paths carry `handoff`), or at the
 * top level (a restored turn). Anything malformed is no information.
 */
export function officerHoursFrom(meta: TurnMeta): OfficerHours | undefined {
  const handoff = meta.handoff as Record<string, unknown> | null | undefined;
  const raw = (meta.officer_hours ?? (handoff && typeof handoff === 'object' ? handoff.officer_hours : undefined)) as
    | Record<string, unknown>
    | null
    | undefined;
  if (!raw || typeof raw !== 'object') return undefined;
  const onDuty = raw.on_duty ?? raw.onDuty;
  if (typeof onDuty !== 'boolean' || typeof raw.hours !== 'string') return undefined;
  const next = raw.next_on_duty ?? raw.nextOnDuty;
  return { onDuty, hours: raw.hours.slice(0, 64), nextOnDuty: typeof next === 'string' ? next : null };
}

function discrepancyFrom(value: unknown, fallback: ChatTurn['discrepancyReport']): ChatTurn['discrepancyReport'] {
  if (!value || typeof value !== 'object') return fallback;
  const d = value as Record<string, unknown>;
  return {
    id: String(d.id || ''),
    status: String(d.status || 'pending'),
    frequency_count: typeof d.frequency_count === 'number' ? d.frequency_count : 1,
    discrepancy_type: typeof d.discrepancy_type === 'string' ? d.discrepancy_type : 'outdated_law',
  };
}

/**
 * The turn with the server's metadata applied — one place for every frame
 * that carries it. A field the frame does not carry keeps the turn's value.
 *
 * Three hand-copied versions of this drifted apart: the one for metadata
 * lines never copied `next_actions`, so no suggested action — the guided
 * steps, an officer, the crisis reply's officer — reached the web chat,
 * though every API response carried them (found 2026-10-16, Phase 39).
 */
export function applyTurnMeta(turn: ChatTurn, meta: TurnMeta, notices: ConnectorNotice[] = []): ChatTurn {
  const workflow = (meta.workflow as WorkflowState | undefined) ?? turn.workflow;
  const nextActions = Array.isArray(meta.next_actions)
    ? meta.next_actions.filter((action): action is string => typeof action === 'string')
    : turn.nextActions;
  return {
    ...turn,
    serverTurnId: typeof meta.turn_id === 'string' ? meta.turn_id : turn.serverTurnId,
    connectorNotices: notices.length ? notices : turn.connectorNotices,
    citations: (meta.citations as Citation[] | undefined) ?? turn.citations,
    faithfulnessScore: (meta.faithfulness_score as number | null | undefined) ?? turn.faithfulnessScore,
    retrievalMode: (meta.retrieval_mode as string | undefined) ?? turn.retrievalMode,
    escalationRequired: (meta.escalation_required as boolean | undefined) ?? turn.escalationRequired,
    escalationReason: (meta.escalation_reason as string | undefined) ?? turn.escalationReason,
    officerHours: officerHoursFrom(meta) ?? turn.officerHours,
    nextActions,
    workflow,
    objectiveHypothesis:
      ((meta.objective_hypothesis ?? meta.objectiveHypothesis) as ObjectiveHypothesis | null | undefined) ??
      turn.objectiveHypothesis,
    resources:
      (meta.resources as ContextResource[] | undefined) ??
      ((meta.workflow as { resources?: ContextResource[] } | undefined)?.resources ?? turn.resources),
    discrepancyReport: discrepancyFrom(meta.discrepancy_report ?? meta.discrepancyReport, turn.discrepancyReport),
    portalCopilot:
      ((meta.portal_copilot ?? meta.portalCopilot) as PortalCopilotPayload | null | undefined) ?? turn.portalCopilot,
  };
}
