/**
 * Every frame that carries a turn's metadata goes through applyTurnMeta.
 *
 * The SSE handler had three hand-copied versions of this; the one for metadata
 * lines never copied `next_actions`, so the guided-steps offer, "Talk to an
 * officer" and the crisis reply's officer never appeared in the web chat,
 * although every API response carried them (Phase 39, 2026-10-16).
 */
import { describe, expect, it } from 'vitest';

import { applyTurnMeta, formatNextShift, officerHoursFrom } from '../../lib/turnMeta';
import type { ChatTurn } from '../../store/useChatStore';

const turn = (over: Partial<ChatTurn> = {}): ChatTurn =>
  ({ id: 'a1', role: 'assistant', content: '', ...over }) as ChatTurn;

describe('applyTurnMeta', () => {
  it('removes actions and cards explicitly withdrawn by the server', () => {
    const before = turn({ workflow: { id: 'w', name: 'W', status: 'active' },
      nextActions: ['Continue'], portalCopilot: { card_type: 'confirmation_required' } });
    const out = applyTurnMeta(before, { workflow: null, next_actions: null, portal_copilot: null });
    expect(out.workflow).toBeUndefined();
    expect(out.portalCopilot).toBeFalsy();
    expect(out.nextActions).toEqual([]);
  });
  it('clears a grounding score explicitly withdrawn by the final frame', () => {
    expect(applyTurnMeta(turn({ faithfulnessScore: 0.99 }), { faithfulness_score: null }).faithfulnessScore).toBeNull();
  });

  it.each([NaN, Infinity, -1, 1.1, '0.99', {}, false])('rejects invalid grounding evidence %s', (value) => {
    expect(applyTurnMeta(turn(), { faithfulness_score: value }).faithfulnessScore).toBeNull();
  });

  it('drops malformed source rows and malformed inferred objectives', () => {
    const out = applyTurnMeta(turn(), {
      citations: [null, { ref: '[1]', source: 'VAT', passage: {} }],
      resources: [{ title: 'VAT', url: 'https://ura.go.ug', description: [] }],
      objective_hypothesis: { objective_id: 'x', title: {}, confirm_action: ['send'] },
    });
    expect(out.citations).toEqual([]);
    expect(out.resources).toEqual([]);
    expect(out.objectiveHypothesis).toBeNull();
  });

  it('rejects corrupt workflow and portal_copilot cards at ingress (G274)', () => {
    const bad = applyTurnMeta(turn(), {
      workflow: { id: 123, name: null, status: 'bogus', options: 'not-an-array' },
      portal_copilot: { card_type: '', fields: 'not-an-array' },
    });
    expect(bad.workflow).toBeUndefined();
    expect(bad.portalCopilot).toBeNull();
  });
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

  it('names the next shift: weekday, date and time', () => {
    const label = formatNextShift('2026-10-17T07:00:00+03:00', 'en', 'Africa/Kampala');
    expect(label).toContain('Saturday');
    expect(label).toContain('17');
    expect(label).toContain('07:00');
    expect(formatNextShift('soon', 'en')).toBeNull();
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

  it('propagates objective_hypothesis onto turn.objectiveHypothesis (Pillar 4)', () => {
    const hypothesis = {
      objective_id: 'wht_credit_and_filing',
      title: 'Verify 6% Withholding Tax Deduction & Claim Tax Credit',
      domain: 'returns',
      workflow_id: 'return_filing',
      confirm_action: 'Guide me step by step through Return Filing',
      prerequisites_summary: 'WHT Certificate and EFRIS invoice',
    };
    const out = applyTurnMeta(turn(), { objective_hypothesis: hypothesis });
    expect(out.objectiveHypothesis).toEqual(hypothesis);
    // Subsequent metadata frames without objective_hypothesis preserve the existing one
    const preserved = applyTurnMeta(out, { faithfulness_score: 0.91 });
    expect(preserved.objectiveHypothesis).toEqual(hypothesis);
  });
});
