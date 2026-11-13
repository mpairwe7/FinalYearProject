import type { ChatTurn } from '../store/useChatStore';

/**
 * Whether an answer read aloud needs its caveat said too (G646).
 *
 * The screen warns with the "Verify with URA" badge and the human-review
 * banner; a listener in voice mode sees neither. The same signals decide
 * here: a grounding score under 0.6, a judge below `high`, an escalation. An
 * abstention already says it has no answer, and an officer's message is not
 * the assistant's.
 */
export function needsSpokenCaveat(turn: ChatTurn): boolean {
  if (turn.retrievalMode === 'officer_reply' || turn.retrievalMode === 'abstained') return false;
  if (turn.escalationRequired) return true;
  if (turn.confidenceBand === 'medium' || turn.confidenceBand === 'low') return true;
  return turn.faithfulnessScore != null && turn.faithfulnessScore < 0.6;
}

/**
 * The text read aloud for an answer: who is speaking (G647), then the
 * reliability caveat (G646), then the answer. Both notices come first so a
 * listener who stops a long answer early has still heard them.
 */
export function spokenReply(content: string, extra: { caveat?: string; disclosure?: string }): string {
  return [extra.disclosure, extra.caveat, content].filter((part): part is string => Boolean(part)).join('\n\n');
}
