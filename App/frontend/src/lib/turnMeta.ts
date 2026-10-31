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

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

/** Invalid evidence must not produce a reassuring grounding badge or crash a source list. */
function score(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1 ? value : null;
}

function citationsFrom(value: unknown): Citation[] {
  if (!Array.isArray(value)) return [];
  return value.filter((c): c is Citation => record(c) && typeof c.ref === 'string' &&
    typeof c.source === 'string' && ['page', 'section', 'passage', 'url', 'effective_date', 'title']
      .every((key) => c[key] === undefined || typeof c[key] === 'string'));
}

function resourcesFrom(value: unknown): ContextResource[] {
  if (!Array.isArray(value)) return [];
  return value.filter((r): r is ContextResource => record(r) && typeof r.title === 'string' &&
    typeof r.url === 'string' && ['type', 'format', 'size', 'description', 'citation', 'effective_year', 'source_domain']
      .every((key) => r[key] === undefined || typeof r[key] === 'string'));
}

function objectiveFrom(value: unknown): ObjectiveHypothesis | null {
  if (!record(value) || !['objective_id', 'title', 'domain', 'workflow_id', 'confirm_action']
    .every((key) => typeof value[key] === 'string') ||
    !['workflow_name', 'prerequisites_summary'].every((key) => value[key] === undefined || typeof value[key] === 'string')) return null;
  return { ...value, confidence: score(value.confidence) ?? undefined } as unknown as ObjectiveHypothesis;
}

function workflowFrom(value: unknown): WorkflowState | undefined {
  if (!record(value)) return undefined;
  if (typeof value.id !== 'string' || typeof value.name !== 'string' || typeof value.status !== 'string') {
    return undefined;
  }
  if (!['active', 'completed', 'cancelled'].includes(value.status)) {
    return undefined;
  }
  if (value.options !== undefined && !Array.isArray(value.options)) return undefined;
  if (value.all_steps !== undefined && !Array.isArray(value.all_steps)) return undefined;
  return {
    ...value,
    options: Array.isArray(value.options)
      ? value.options.filter((o): o is string => typeof o === 'string').slice(0, 64)
      : undefined,
    all_steps: Array.isArray(value.all_steps)
      ? value.all_steps.filter(
          (s): s is NonNullable<WorkflowState['all_steps']>[number] =>
            record(s) && typeof s.id === 'string' && typeof s.title === 'string' && typeof s.status === 'string',
        )
      : undefined,
  } as unknown as WorkflowState;
}

function portalCopilotFrom(value: unknown): PortalCopilotPayload | null {
  if (!record(value)) return null;
  if (typeof value.card_type !== 'string' || !value.card_type.trim() || value.card_type.length > 128) {
    return null;
  }
  if (value.fields !== undefined && !Array.isArray(value.fields)) return null;
  if (value.display_fields !== undefined && !Array.isArray(value.display_fields)) return null;
  return {
    ...value,
    fields: Array.isArray(value.fields)
      ? value.fields.filter(
          (f): f is NonNullable<PortalCopilotPayload['fields']>[number] =>
            record(f) && typeof f.name === 'string' && typeof f.label === 'string',
        )
      : undefined,
    display_fields: Array.isArray(value.display_fields)
      ? value.display_fields.filter(
          (f): f is NonNullable<PortalCopilotPayload['display_fields']>[number] =>
            record(f) && typeof f.name === 'string' && typeof f.label === 'string' && typeof f.value === 'string',
        )
      : undefined,
  } as unknown as PortalCopilotPayload;
}

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
  if (!record(meta)) return turn;
  const workflow = 'workflow' in meta
    ? workflowFrom(meta.workflow) : turn.workflow;
  const nextActions = Array.isArray(meta.next_actions)
    ? meta.next_actions.filter((action): action is string => typeof action === 'string')
    : ('next_actions' in meta ? [] : turn.nextActions);
  return {
    ...turn,
    serverTurnId: typeof meta.turn_id === 'string' ? meta.turn_id : turn.serverTurnId,
    connectorNotices: notices.length ? notices : turn.connectorNotices,
    citations: 'citations' in meta ? citationsFrom(meta.citations) : turn.citations,
    faithfulnessScore: 'faithfulness_score' in meta ? score(meta.faithfulness_score) : turn.faithfulnessScore,
    retrievalMode: typeof meta.retrieval_mode === 'string' ? meta.retrieval_mode : turn.retrievalMode,
    escalationRequired: typeof meta.escalation_required === 'boolean' ? meta.escalation_required : turn.escalationRequired,
    escalationReason: typeof meta.escalation_reason === 'string' ? meta.escalation_reason : turn.escalationReason,
    officerHours: officerHoursFrom(meta) ?? turn.officerHours,
    nextActions,
    workflow,
    objectiveHypothesis: 'objective_hypothesis' in meta || 'objectiveHypothesis' in meta
      ? objectiveFrom(meta.objective_hypothesis ?? meta.objectiveHypothesis) : turn.objectiveHypothesis,
    resources:
      'resources' in meta ? resourcesFrom(meta.resources) :
        (record(meta.workflow) && 'resources' in meta.workflow ? resourcesFrom(meta.workflow.resources) : turn.resources),
    discrepancyReport: discrepancyFrom(meta.discrepancy_report ?? meta.discrepancyReport, turn.discrepancyReport),
    portalCopilot: 'portal_copilot' in meta || 'portalCopilot' in meta
      ? portalCopilotFrom(meta.portal_copilot ?? meta.portalCopilot) : turn.portalCopilot,
  };
}
