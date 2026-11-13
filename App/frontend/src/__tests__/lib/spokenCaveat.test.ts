/**
 * Voice mode says aloud what the screen says about an answer's reliability
 * (G646). A low-confidence answer, one the judge doubted, or one that escalated
 * sounded exactly like a well-supported one: the "Verify with URA" badge and
 * the human-review banner exist only on screen.
 */
import { describe, expect, it } from 'vitest';

import { needsSpokenCaveat, spokenReply } from '../../lib/spokenCaveat';
import type { ChatTurn } from '../../store/useChatStore';

const turn = (over: Partial<ChatTurn>): ChatTurn => ({ id: 'a', role: 'assistant', content: 'x', timestamp: 0, ...over });

describe('needsSpokenCaveat', () => {
  it.each([
    ['a low grounding score', { faithfulnessScore: 0.4 }],
    ['a judge that is not confident', { faithfulnessScore: 0.9, confidenceBand: 'medium' as const }],
    ['an escalated answer', { escalationRequired: true }],
    ['an unconfirmed calculation', { retrievalMode: 'calculator', confidenceBand: 'medium' as const }],
  ])('is true for %s', (_name, over) => {
    expect(needsSpokenCaveat(turn(over))).toBe(true);
  });

  it.each([
    ['a well-grounded answer', { faithfulnessScore: 0.9, confidenceBand: 'high' as const }],
    ['a confirmed calculation', { retrievalMode: 'calculator', confidenceBand: 'high' as const }],
    ['an abstention, which says so itself', { retrievalMode: 'abstained' }],
    ['an officer’s message', { retrievalMode: 'officer_reply', faithfulnessScore: 0.1 }],
  ])('is false for %s', (_name, over) => {
    expect(needsSpokenCaveat(turn(over))).toBe(false);
  });
});

// Self-review on PR (phase 5): a caveat read after a long answer is lost on a
// listener who stops early, so it is said before the answer.
describe("spokenReply", () => {
  it("says the AI notice, then the caveat, then the answer", () => {
    expect(
      spokenReply("VAT is 18%.", { caveat: "Please note: this answer may not be reliable.", disclosure: "This is an AI." }),
    ).toBe("This is an AI.\n\nPlease note: this answer may not be reliable.\n\nVAT is 18%.");
  });

  it("is the answer alone when there is nothing to add", () => {
    expect(spokenReply("VAT is 18%.", {})).toBe("VAT is 18%.");
  });
});

