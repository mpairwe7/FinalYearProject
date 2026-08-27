"use client";

/**
 * Settings — one dialog, a tab per group of controls.
 *
 * Layout follows the pattern people already know from Grok and ChatGPT: a
 * vertical tab rail on the left, one scrolling pane on the right, and the
 * dialog centred over a dimmed page. Below 720px the rail becomes a horizontal
 * strip so the pane keeps the full width of a phone.
 *
 * The shell owns three things the sections should not each re-implement:
 * focus containment while open, the confirmation step in front of destructive
 * actions (reusing the app's ConfirmDialog), and which tab is showing.
 *
 * Accessibility: role=dialog + aria-modal, tablist/tab/tabpanel semantics with
 * arrow-key movement and roving tabindex, Escape and overlay click dismiss
 * (both deferred while a confirmation is up), and focus returns to whatever
 * opened the dialog.
 */

import React, { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CloseIcon } from "../Icons";
import ConfirmDialog, { type ConfirmRequest } from "../ConfirmDialog";
import { useIdentity } from "../../hooks/useIdentity";
import AccountSection from "./AccountSection";
import GeneralSection from "./GeneralSection";
import PrivacySection from "./PrivacySection";
import ProfileSection from "./ProfileSection";
import VoiceSection from "./VoiceSection";
import { restoreFocus } from "../../lib/focus";

export type SettingsTab = "general" | "voice" | "profile" | "privacy" | "account";

const TABS: readonly { id: SettingsTab; label: string }[] = [
  { id: "general", label: "General" },
  { id: "voice", label: "Voice" },
  { id: "profile", label: "Tax profile" },
  { id: "privacy", label: "Privacy & data" },
  { id: "account", label: "Account" },
];

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

interface SettingsDialogProps {
  open: boolean;
  onClose: () => void;
  /**
   * Which section is showing. Controlled by the caller so opening the dialog
   * can land on a specific tab — the landing page's "Account & settings" link
   * opens Account — without this component resetting its own state in an effect.
   */
  tab: SettingsTab;
  onTabChange: (tab: SettingsTab) => void;
  /**
   * Which sections to show, in order. Defaults to all of them — the taxpayer
   * chat wants the lot. The operations console passes a subset: Voice narrates
   * an answer that surface never renders, and Tax profile is a taxpayer's own
   * TIN, so an officer's console offers neither.
   */
  tabs?: readonly SettingsTab[];
  /**
   * Narration is page state (the composer's voice mode also drives it), so it
   * is supplied by whoever owns that state. Optional because a caller that
   * excludes the Voice tab has none to give.
   */
  autoNarrate?: boolean;
  onAutoNarrateChange?: (on: boolean) => void;
  speechReady?: boolean;
  /** Omitted by callers with no blog to link — the footer link is then absent. */
  blogUrl?: string;
}

const NO_OP = () => {};

export default function SettingsDialog({
  open,
  onClose,
  tab,
  onTabChange,
  tabs: allowedTabs,
  autoNarrate = false,
  onAutoNarrateChange = NO_OP,
  speechReady = false,
  blogUrl,
}: SettingsDialogProps) {
  const [confirmReq, setConfirmReq] = useState<ConfirmRequest | null>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const tabRefs = useRef<(HTMLButtonElement | null)[]>([]);
  const identity = useIdentity();

  // Order comes from TABS, not from the caller's array, so two callers asking
  // for the same sections always present them in the same order.
  const tabs = useMemo(
    () => (allowedTabs ? TABS.filter((t) => allowedTabs.includes(t.id)) : TABS),
    [allowedTabs],
  );

  /**
   * Take focus on open, give it back on close, and lock the page behind.
   *
   * Depends on `open` ALONE, deliberately. With the handlers in the dependency
   * list it re-ran on every render — a parent passing a fresh `onClose` closure
   * is enough — and each re-run pulled focus back to the panel, so arrow-keying
   * through the tab list moved the selection once and then went dead. Running
   * once per open is also what makes `previouslyFocused` the element that opened
   * the dialog, rather than whatever was focused at the last re-render.
   */
  useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    // Focus the dialog itself rather than the first control: the first control
    // is a tab, and moving focus into the rail reads as "you are in a menu".
    panelRef.current?.focus();

    return () => {
      document.body.style.overflow = prevOverflow;
      // Return to the opener when it still exists. If it was a menu item that
      // unmounted as the dialog opened, restoreFocus() puts focus on the main
      // landmark rather than silently leaving it on <body>.
      restoreFocus(previouslyFocused);
    };
  }, [open]);

  // Escape + the Tab trap. Rebinding a listener is cheap, so this one may
  // depend on the handlers it calls.
  useEffect(() => {
    if (!open) return;

    const onKey = (e: KeyboardEvent) => {
      // While a confirmation is up it owns the keyboard — otherwise one Escape
      // would dismiss both it and the settings behind it.
      if (confirmReq) return;
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
        return;
      }
      if (e.key !== "Tab") return;
      const focusables = panelRef.current?.querySelectorAll<HTMLElement>(FOCUSABLE);
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
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose, confirmReq]);

  const onTabKey = useCallback(
    (e: React.KeyboardEvent, index: number) => {
      const move = (next: number) => {
        const clamped = (next + tabs.length) % tabs.length;
        onTabChange(tabs[clamped].id);
        tabRefs.current[clamped]?.focus();
      };
      if (e.key === "ArrowDown" || e.key === "ArrowRight") {
        e.preventDefault();
        move(index + 1);
      } else if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
        e.preventDefault();
        move(index - 1);
      } else if (e.key === "Home") {
        e.preventDefault();
        move(0);
      } else if (e.key === "End") {
        e.preventDefault();
        move(tabs.length - 1);
      }
    },
    [onTabChange, tabs],
  );

  const requestConfirm = useCallback((request: ConfirmRequest) => {
    setConfirmReq(request);
  }, []);

  // Deliberately NOT collapsed to a boolean before it reaches the sections:
  // "checking", "rejected" and "unavailable" are all !signedIn, but telling
  // someone whose backend is down that they need to sign in is wrong, and the
  // in-flight case would flash that message on every open.
  const identityStatus = identity.status;

  const body = useMemo(() => {
    switch (tab) {
      case "general":
        return <GeneralSection />;
      case "voice":
        return (
          <VoiceSection
            autoNarrate={autoNarrate}
            onAutoNarrateChange={onAutoNarrateChange}
            speechReady={speechReady}
          />
        );
      case "profile":
        return <ProfileSection status={identityStatus} />;
      case "privacy":
        return (
          <PrivacySection
            status={identityStatus}
            requestConfirm={requestConfirm}
            onSignedOut={identity.signOut}
          />
        );
      case "account":
        return <AccountSection state={identity} />;
    }
  }, [
    tab,
    autoNarrate,
    onAutoNarrateChange,
    speechReady,
    identityStatus,
    requestConfirm,
    identity,
  ]);

  if (!open) return null;

  return (
    <>
      <div
        className="setv2-overlay"
        onMouseDown={(e) => {
          if (confirmReq) return;
          if (e.target === e.currentTarget) onClose();
        }}
      >
        <div
          ref={panelRef}
          className="setv2"
          role="dialog"
          aria-modal="true"
          aria-labelledby="setv2-title"
          tabIndex={-1}
        >
          <header className="setv2-head">
            <h2 id="setv2-title">Settings</h2>
            <button
              type="button"
              className="dlgv2-x setv2-x"
              onClick={onClose}
              aria-label="Close settings"
            >
              <CloseIcon />
            </button>
          </header>

          <div className="setv2-body">
            <div className="setv2-tabs" role="tablist" aria-label="Settings sections" aria-orientation="vertical">
              {tabs.map((t, i) => (
                <button
                  key={t.id}
                  ref={(el) => {
                    tabRefs.current[i] = el;
                  }}
                  type="button"
                  role="tab"
                  id={`setv2-tab-${t.id}`}
                  aria-selected={tab === t.id}
                  aria-controls={`setv2-panel-${t.id}`}
                  tabIndex={tab === t.id ? 0 : -1}
                  className={`setv2-tab${tab === t.id ? " setv2-tab-active" : ""}`}
                  onClick={() => onTabChange(t.id)}
                  onKeyDown={(e) => onTabKey(e, i)}
                >
                  {t.label}
                </button>
              ))}
            </div>

            <div
              className="setv2-pane"
              role="tabpanel"
              id={`setv2-panel-${tab}`}
              aria-labelledby={`setv2-tab-${tab}`}
              tabIndex={0}
            >
              {body}
            </div>
          </div>

          <footer className="setv2-foot">
            <span>URA Tax Assistant — answers cite the URA sources they came from.</span>
            {blogUrl ? (
              <a href={blogUrl} target="_blank" rel="noopener noreferrer">
                Project blog ↗
              </a>
            ) : null}
          </footer>
        </div>
      </div>

      {/* Destructive actions from any section land here. */}
      <ConfirmDialog
        confirm={confirmReq}
        onClose={(run) => {
          const request = confirmReq;
          setConfirmReq(null);
          if (run) request?.action();
        }}
      />
    </>
  );
}
