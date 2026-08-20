/** Shared focus utilities for modal UI.
 *
 * Modal components can unmount the control that opened them (for example a
 * menu item). In that case returning focus to `document.body` strands keyboard
 * and screen-reader users at the top of the page. The main landmark is a safe,
 * meaningful fallback that can be focused programmatically.
 */
export function restoreFocus(
  previous: HTMLElement | null | undefined,
  fallbackSelector = "#main-content, main, [role='main']",
): void {
  if (previous?.isConnected) {
    previous.focus({ preventScroll: true });
    return;
  }

  const fallback = document.querySelector<HTMLElement>(fallbackSelector);
  if (!fallback) return;
  // A main landmark is not keyboard-focusable by default, but tabindex=-1
  // makes it a valid programmatic destination without adding a Tab stop.
  if (!fallback.hasAttribute("tabindex")) fallback.tabIndex = -1;
  fallback.focus({ preventScroll: true });
}
