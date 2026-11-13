/**
 * The browser's reasoning filter never removes what the server says about an
 * answer's limits (G621).
 *
 * `cleanResponse` strips leaked model reasoning: when an answer opens with a
 * paragraph that reads like thinking ("I can help with that.", "Sure, let me
 * explain."), it kept only the last run of answer paragraphs and dropped any
 * trailing paragraph that matched a reasoning pattern. The server appends its
 * disclosures as trailing paragraphs, and two of them match: "…so this may not
 * include recent changes" (`not include`) and "This is taking longer than it
 * should…" (`this is`). Both were deleted before the taxpayer saw them, and the
 * stripped text is what history keeps.
 */
import { describe, expect, it } from 'vitest';

import { cleanResponse } from '../../store/useChatStore';

// Verbatim from App/backend/app/web_research.py (LIVE_UNAVAILABLE) and
// App/backend/app/service.py (TURN_DEADLINE_MESSAGE).
const LIVE_UNAVAILABLE =
  "I could not check URA's website just now, so this may not include recent changes. " +
  'Please confirm at https://ura.go.ug or on 0800 117 000.';
const TURN_DEADLINE =
  'This is taking longer than it should, so I have stopped rather than give you a ' +
  'partial answer. Please try again, or contact URA on 0800 117 000.';

describe('cleanResponse keeps the server\'s disclosures', () => {
  it.each([
    ['I can help with that.', LIVE_UNAVAILABLE],
    ['Sure, let me explain.', LIVE_UNAVAILABLE],
    ['I can help with that.', TURN_DEADLINE],
    ['Sure, let me explain.', TURN_DEADLINE],
  ])('after an opener like %j it keeps the closing notice', (opener, notice) => {
    const cleaned = cleanResponse(`${opener}\n\nThe standard VAT rate is 18% [1].\n\n${notice}`);
    expect(cleaned).toContain('The standard VAT rate is 18%');
    expect(cleaned).toContain(notice.slice(0, 40));
  });

  it('still drops leaked reasoning ahead of the answer', () => {
    const cleaned = cleanResponse(
      'Okay, the user wants the VAT rate.\n\nLooking at passage [1], it says 18%.\n\nThe standard VAT rate is 18% [1].',
    );
    expect(cleaned).toBe('The standard VAT rate is 18% [1].');
  });
});
