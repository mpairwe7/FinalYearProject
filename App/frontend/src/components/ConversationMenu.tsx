"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { restoreFocus } from "../lib/focus";
import { PencilIcon, PinIcon, PinOffIcon, TrashIcon } from "./Icons";

/**
 * Pin / Rename / Delete for one conversation.
 *
 * One component serves both callers — the kebab on a sidebar row and the
 * chevron beside the chat title in the top strip — so the two menus cannot
 * drift apart in wording, ordering or keyboard behaviour. Only the trigger
 * differs, which is why it is a render prop.
 *
 * Keyboard follows the WAI-ARIA menu-button pattern already established by
 * ChatHeader: focus lands on the first item as the menu opens, Arrow keys and
 * Home/End move within it, Escape closes and hands focus back to the trigger.
 *
 * The panel is portalled to <body> rather than positioned in place. The rail
 * clips its children (`overflow: hidden`) and, as a mobile drawer, carries a
 * transform — which makes it a containing block, so even `position: fixed`
 * would be trapped inside it.
 */

interface ConversationMenuProps {
  pinned: boolean;
  onPin: () => void;
  onRename: () => void;
  onDelete: () => void;
  /** Accessible name for the trigger, e.g. `Options for "VAT rates"`. */
  triggerLabel: string;
  triggerClassName?: string;
  triggerChildren: React.ReactNode;
  /** Which edge of the trigger the panel aligns to. */
  align?: "start" | "end";
}

const PANEL_WIDTH = 184;
const PANEL_GAP = 6;

export default function ConversationMenu({
  pinned,
  onPin,
  onRename,
  onDelete,
  triggerLabel,
  triggerClassName = "",
  triggerChildren,
  align = "end",
}: ConversationMenuProps) {
  /**
   * Position is measured as the menu opens, in the handler, rather than
   * corrected afterwards in a layout effect — so `open` and the coordinates
   * land in the same render and the panel never paints at 0,0 first.
   */
  const [pos, setPos] = useState<{ top: number; left: number } | null>(null);
  const open = pos !== null;
  const triggerRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const itemRefs = useRef<(HTMLButtonElement | null)[]>([]);

  const close = useCallback((restore = false) => {
    setPos(null);
    if (restore) restoreFocus(triggerRef.current);
  }, []);

  const openMenu = useCallback(() => {
    const rect = triggerRef.current?.getBoundingClientRect();
    if (!rect) return;
    const left =
      align === "end"
        ? Math.min(rect.right - PANEL_WIDTH, window.innerWidth - PANEL_WIDTH - 8)
        : Math.min(rect.left, window.innerWidth - PANEL_WIDTH - 8);
    setPos({ top: rect.bottom + PANEL_GAP, left: Math.max(8, left) });
  }, [align]);

  const runThenClose = (action: () => void) => {
    close(true);
    action();
  };

  useEffect(() => {
    if (!open) return;
    itemRefs.current[0]?.focus();

    const onDoc = (e: MouseEvent) => {
      const target = e.target as Node;
      if (panelRef.current?.contains(target) || triggerRef.current?.contains(target)) return;
      setPos(null);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") close(true);
    };
    // A menu anchored to a viewport coordinate has to go away when that
    // coordinate moves, rather than float free of the row it belongs to.
    const onReflow = () => setPos(null);

    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    window.addEventListener("resize", onReflow);
    window.addEventListener("scroll", onReflow, true);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onReflow);
      window.removeEventListener("scroll", onReflow, true);
    };
  }, [open, close]);

  const onMenuKeyDown = (event: React.KeyboardEvent<HTMLElement>) => {
    const items = itemRefs.current.filter((i): i is HTMLButtonElement => Boolean(i));
    if (!items.length) return;
    const current = Math.max(0, items.indexOf(event.currentTarget as HTMLButtonElement));
    const move = (next: number) => items[(next + items.length) % items.length]?.focus();

    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        move(current + 1);
        break;
      case "ArrowUp":
        event.preventDefault();
        move(current - 1);
        break;
      case "Home":
        event.preventDefault();
        items[0]?.focus();
        break;
      case "End":
        event.preventDefault();
        items.at(-1)?.focus();
        break;
      case "Escape":
        event.preventDefault();
        event.stopPropagation();
        close(true);
        break;
    }
  };

  const panel = pos && typeof document !== "undefined"
    ? createPortal(
        <div
          ref={panelRef}
          className="convmenu chatv2"
          role="menu"
          aria-label={triggerLabel}
          style={{ top: pos.top, left: pos.left, width: PANEL_WIDTH }}
        >
          <button
            ref={(el) => {
              itemRefs.current[0] = el;
            }}
            type="button"
            role="menuitem"
            className="convmenu-item"
            onClick={() => runThenClose(onPin)}
            onKeyDown={onMenuKeyDown}
          >
            {pinned ? <PinOffIcon /> : <PinIcon />}
            {pinned ? "Unpin" : "Pin"}
          </button>
          <button
            ref={(el) => {
              itemRefs.current[1] = el;
            }}
            type="button"
            role="menuitem"
            className="convmenu-item"
            onClick={() => runThenClose(onRename)}
            onKeyDown={onMenuKeyDown}
          >
            <PencilIcon /> Rename
          </button>
          <button
            ref={(el) => {
              itemRefs.current[2] = el;
            }}
            type="button"
            role="menuitem"
            className="convmenu-item convmenu-item-danger"
            onClick={() => runThenClose(onDelete)}
            onKeyDown={onMenuKeyDown}
          >
            <TrashIcon /> Delete
          </button>
        </div>,
        document.body,
      )
    : null;

  return (
    <>
      <button
        ref={triggerRef}
        type="button"
        className={triggerClassName}
        aria-label={triggerLabel}
        aria-haspopup="menu"
        aria-expanded={open}
        onClick={(e) => {
          e.stopPropagation();
          if (open) close();
          else openMenu();
        }}
        onKeyDown={(e) => {
          if (!open && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
            e.preventDefault();
            openMenu();
          }
        }}
      >
        {triggerChildren}
      </button>
      {panel}
    </>
  );
}
