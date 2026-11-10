/**
 * Guards on the Allow button of a confirmation card (ADR-0038, G549).
 *
 * Neither tells a person from an agent that drives the browser's real input,
 * and nothing in a page can. The server decides what an approval may do; these
 * stop the two cheap ways to approve without anyone looking.
 */

/**
 * How long a confirmation is on screen before Allow can be pressed. A card that
 * appears under a click already on its way does not take that click as consent,
 * the same delay browsers put on their own permission prompts.
 */
export const CONFIRM_ARM_DELAY_MS = 800;

/**
 * True for a click the browser raised from real input: a pointer, a key, or
 * assistive technology. `element.click()` and `dispatchEvent` from a page
 * script or an extension set `isTrusted` to false.
 */
export function isBrowserRaisedClick(event: {
  isTrusted?: boolean;
  nativeEvent?: { isTrusted?: boolean };
}): boolean {
  return (event.nativeEvent?.isTrusted ?? event.isTrusted) === true;
}
