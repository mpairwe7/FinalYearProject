import { type KeyboardEvent as ReactKeyboardEvent, type RefObject, useEffect, useRef } from 'react';
import { restoreFocus } from '@/lib/focus';

const FOCUSABLE = [
  'a[href]',
  'button:not([disabled])',
  'input:not([disabled])',
  'select:not([disabled])',
  'textarea:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(',');

function focusables(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE));
}

/**
 * Focus for a modal that is not a native `<dialog>`: the call screen, whose
 * Escape and backdrop must not close a live call the way `ModalDialog`'s do.
 *
 * - When *view* first becomes non-null the element that had focus is kept,
 *   and it gets focus back when *view* returns to null.
 * - Whenever *view* changes and focus is not inside *container*, focus moves
 *   to `[data-dialog-initial-focus]` or else the first focusable element: the
 *   call screen swaps its whole card when a call starts, which used to drop
 *   focus to the page behind it.
 * - The returned key handler wraps Tab and Shift+Tab inside *container*.
 */
export function useDialogFocus(
  container: RefObject<HTMLElement | null>,
  view: string | null,
): (event: ReactKeyboardEvent<HTMLElement>) => void {
  const opener = useRef<HTMLElement | null>(null);
  const open = view !== null;

  useEffect(() => {
    if (!open) return undefined;
    const active = document.activeElement;
    opener.current = active instanceof HTMLElement && active !== document.body ? active : null;
    return () => {
      restoreFocus(opener.current);
      opener.current = null;
    };
  }, [open]);

  useEffect(() => {
    const root = container.current;
    if (!view || !root || root.contains(document.activeElement)) return;
    const target = root.querySelector<HTMLElement>('[data-dialog-initial-focus]') ?? focusables(root)[0] ?? root;
    target.focus({ preventScroll: true });
  }, [container, view]);

  return (event: ReactKeyboardEvent<HTMLElement>) => {
    const root = container.current;
    if (event.key !== 'Tab' || !root) return;
    const items = focusables(root);
    if (items.length === 0) {
      event.preventDefault();
      return;
    }
    const first = items[0];
    const last = items[items.length - 1];
    const active = document.activeElement;
    // Focus on the dialog itself (the call card) is before every control.
    const beforeFirst = active === first || !root.contains(active) || !items.includes(active as HTMLElement);
    if (event.shiftKey && beforeFirst) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && (active === last || !root.contains(active))) {
      event.preventDefault();
      first.focus();
    }
  };
}
