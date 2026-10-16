import type { ChatTurn, Citation, ContextResource, WorkflowState } from '../store/useChatStore';
import type { ConnectorNotice } from './connectors';

/** A turn's metadata as the server sends it (SSE `metadata`/`grounding`/`done`, or a metadata line). */
export type TurnMeta = Record<string, unknown>;

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
    nextActions,
    workflow,
    resources:
      (meta.resources as ContextResource[] | undefined) ??
      ((meta.workflow as { resources?: ContextResource[] } | undefined)?.resources ?? turn.resources),
    discrepancyReport: discrepancyFrom(meta.discrepancy_report ?? meta.discrepancyReport, turn.discrepancyReport),
  };
}
