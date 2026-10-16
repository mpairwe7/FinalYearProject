/**
 * Every frame that carries a turn's metadata goes through applyTurnMeta.
 *
 * The SSE handler had three hand-copied versions of this; the one for metadata
 * lines never copied `next_actions`, so the guided-steps offer, "Talk to an
 * officer" and the crisis reply's officer never appeared in the web chat,
 * although every API response carried them (Phase 39, 2026-10-16).
 */
import { describe, expect, it } from 'vitest';

import { applyTurnMeta, officerHoursFrom } from '../../lib/turnMeta';
import type { ChatTurn } from '../../store/useChatStore';

const turn = (over: Partial<ChatTurn> = {}): ChatTurn =>
  ({ id: 'a1', role: 'assistant', content: '', ...over }) as ChatTurn;

describe('applyTurnMeta', () => {
  it('carries the suggested actions a metadata line brings', () => {
    const line = {
      retrieval_mode: 'workflow',
      next_actions: ['Talk to an officer', 'Continue the steps', 'Cancel'],
      workflow: { id: 'return_filing', name: 'Return Filing', status: 'active', options: [], paused: true },
    };
    const out = applyTurnMeta(turn(), line);
    expect(out.nextActions).toEqual(['Talk to an officer', 'Continue the steps', 'Cancel']);
    expect(out.retrievalMode).toBe('workflow');
    expect(out.workflow?.name).toBe('Return Filing');
  });

  it("takes the officers' hours from the handoff an escalation carries (G196)", () => {
    const frame = {
      escalation_required: true,
      handoff: { topic: 'general', officer_hours: { on_duty: false, hours: '07:00–23:00 EAT', next_on_duty: '2026-10-17T07:00:00+03:00' } },
    };
    expect(applyTurnMeta(turn(), frame).officerHours).toEqual({
      onDuty: false,
      hours: '07:00–23:00 EAT',
      nextOnDuty: '2026-10-17T07:00:00+03:00',
    });
    // A later frame without them keeps them.
    const later = applyTurnMeta(applyTurnMeta(turn(), frame), { next_actions: [] });
    expect(later.officerHours?.onDuty).toBe(false);
  });

  it('reads a restored turn and ignores malformed hours', () => {
    expect(officerHoursFrom({ officer_hours: { onDuty: true, hours: '07:00–23:00 EAT', nextOnDuty: null } })).toEqual({
      onDuty: true,
      hours: '07:00–23:00 EAT',
      nextOnDuty: null,
    });
    expect(officerHoursFrom({ handoff: { officer_hours: { on_duty: 'no', hours: 7 } } })).toBeUndefined();
    expect(officerHoursFrom({ handoff: null })).toBeUndefined();
  });

  it('keeps what the frame does not carry', () => {
    const before = turn({ nextActions: ['Guide me step by step through Return Filing'], retrievalMode: 'hybrid', faithfulnessScore: 0.9 });
    const out = applyTurnMeta(before, { faithfulness_score: 0.95 });
    expect(out.nextActions).toEqual(['Guide me step by step through Return Filing']);
    expect(out.retrievalMode).toBe('hybrid');
    expect(out.faithfulnessScore).toBe(0.95);
  });

  it('drops anything in next_actions that is not a string', () => {
    const out = applyTurnMeta(turn(), { next_actions: ['Talk to an officer', 3, null, { a: 1 }] });
    expect(out.nextActions).toEqual(['Talk to an officer']);
  });

  it('takes resources from the workflow when the frame has none of its own', () => {
    const resources = [{ id: 'r1', title: 'Return form', url: 'https://ura.go.ug/r1' }];
    const out = applyTurnMeta(turn(), { workflow: { id: 'w', name: 'W', status: 'active', resources } });
    expect(out.resources).toEqual(resources);
  });

  it('fills a discrepancy report with safe defaults', () => {
    const out = applyTurnMeta(turn(), { discrepancy_report: { id: 7 } });
    expect(out.discrepancyReport).toEqual({ id: '7', status: 'pending', frequency_count: 1, discrepancy_type: 'outdated_law' });
  });

  it('replaces connector notices only when the frame brings some', () => {
    const notices = [{ namespace: 'efris', label: 'EFRIS', reason: 'unavailable', message: 'down' }];
    const kept = applyTurnMeta(turn({ connectorNotices: notices as ChatTurn['connectorNotices'] }), {});
    expect(kept.connectorNotices).toEqual(notices);
    const fresh = [{ namespace: 'ura', label: 'URA', reason: 'unavailable', message: 'down' }];
    const replaced = applyTurnMeta(
      turn({ connectorNotices: notices as ChatTurn['connectorNotices'] }),
      {},
      fresh as ChatTurn['connectorNotices'],
    );
    expect(replaced.connectorNotices).toEqual(fresh);
  });
});
