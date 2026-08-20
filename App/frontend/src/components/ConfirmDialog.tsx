"use client";

import React, { useEffect, useRef } from "react";
import { CloseIcon } from "./Icons";
import { restoreFocus } from "../lib/focus";

/**
 * Confirmation dialog for destructive actions (delete conversation, clear
 * chat). The confirmed handler is exactly the store call the old UI ran
 * directly — this component only inserts a deliberate step in front of
 * permanent data loss.
 *
 * Accessibility: role=alertdialog, focus starts on Cancel (the safe action),
 * Tab is trapped, Escape / overlay / ✕ dismiss without running the action,
 * and focus returns to the trigger on close.
 */

export interface ConfirmRequest {
  title: string;
  message: string;
  confirmLabel: string;
  danger?: boolean;
  action: () => void;
}

interface ConfirmDialogProps {
  confirm: ConfirmRequest | null;
  onClose: (run: boolean) => void;
}

const WarnIcon = () => (
  <svg aria-hidden="true" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
    <path d="m10.3 3.9-8.2 14A2 2 0 0 0 3.8 21h16.4a2 2 0 0 0 1.7-3.1l-8.2-14a2 2 0 0 0-3.4 0z" />
    <path d="M12 9v4m0 4h.01" />
  </svg>
);

export default function ConfirmDialog({ confirm, onClose }: ConfirmDialogProps) {
  const panelRef = useRef<HTMLDivElement>(null);
  const cancelRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!confirm) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    cancelRef.current?.focus();
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose(false);
        return;
      }
      if (e.key !== "Tab") return;
      const focusables = panelRef.current?.querySelectorAll<HTMLElement>(
        "button:not([disabled])",
      );
      if (!focusables || focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = prevOverflow;
      restoreFocus(previouslyFocused);
    };
  }, [confirm, onClose]);

  if (!confirm) return null;

  return (
    <div
      className="dlgv2-overlay"
      onMouseDown={(e) => e.target === e.currentTarget && onClose(false)}
    >
      <div
        ref={panelRef}
        className="dlgv2"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="dlgv2-title"
        aria-describedby="dlgv2-msg"
      >
        <button className="dlgv2-x" onClick={() => onClose(false)} aria-label="Close dialog">
          <CloseIcon />
        </button>
        <div className="dlgv2-head">
          {confirm.danger && (
            <span className="dlgv2-icon" aria-hidden="true">
              <WarnIcon />
            </span>
          )}
          <h2 id="dlgv2-title">{confirm.title}</h2>
        </div>
        <p id="dlgv2-msg">{confirm.message}</p>
        <div className="dlgv2-row">
          <button ref={cancelRef} className="dlgv2-btn" onClick={() => onClose(false)}>
            Cancel
          </button>
          <button
            className={`dlgv2-btn ${confirm.danger ? "dlgv2-danger" : ""}`}
            onClick={() => onClose(true)}
          >
            {confirm.confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
