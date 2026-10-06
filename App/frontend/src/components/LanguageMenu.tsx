"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";
import { CheckIcon, ChevronDownIcon, GlobeIcon, CloseIcon } from "./Icons";
import { useTranslation } from "../lib/i18n";

/**
 * Response-language picker for the composer toolbar.
 *
 * The trigger is one compact toolbar button; the options open as a centred,
 * focus-trapped overlay (an anchored popover clips against the header when the
 * composer sits mid-screen on the landing view). Options keep the radio
 * semantics of the old header segmented control — role="radio" inside a
 * radiogroup labelled "Language selection" — so existing tests and assistive
 * tech behavior carry over. Selecting a language closes the overlay and
 * returns focus to the trigger.
 *
 * With `onAutoDetect`, the first option is "Auto-detect": the answer follows
 * the language the taxpayer types (backend `app.language_state`), and `locale`
 * is then only the language the assistant last answered in. Picking a
 * language makes it the taxpayer's choice until they pick Auto-detect again.
 */

export interface LanguageOption {
  value: string;
  label: string;
  /** The language's own name for itself, shown under the English label. */
  native?: string;
}

interface LanguageMenuProps {
  locale: string;
  options: readonly LanguageOption[];
  onLocaleChange: (code: string) => void;
  /** True while the answer language is auto-detected. */
  autoDetect?: boolean;
  /** Offer an "Auto-detect" option; called when it is chosen. */
  onAutoDetect?: () => void;
}

const AUTO = "auto";

export default function LanguageMenu({
  locale,
  options: languageOptions,
  onLocaleChange,
  autoDetect = false,
  onAutoDetect,
}: LanguageMenuProps) {
  const t = useTranslation();
  const [open, setOpen] = useState(false);
  /** null = follow the selected locale; a number = the user has arrowed away. */
  const [focusIdx, setFocusIdx] = useState<number | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const optionRefs = useRef<(HTMLButtonElement | null)[]>([]);

  const current = languageOptions.find((o) => o.value === locale) ?? languageOptions[0];
  const auto = Boolean(onAutoDetect) && autoDetect;
  const options: readonly LanguageOption[] = onAutoDetect
    ? [
        { value: AUTO, label: "Auto-detect", native: "Answers follow the language you type" },
        ...languageOptions,
      ]
    : languageOptions;
  const isChecked = (value: string) => (value === AUTO ? auto : !auto && value === locale);
  const activeIdx = Math.max(0, options.findIndex((o) => isChecked(o.value)));
  /* Roving tabindex. Derived rather than stored, so a controlled open — which
     never runs openMenu() — still lands on the selected language. */
  const rovingIdx = focusIdx ?? activeIdx;

  const openMenu = useCallback(() => {
    setFocusIdx(null);
    setOpen(true);
  }, []);

  const close = useCallback(() => {
    setFocusIdx(null);
    setOpen(false);
    btnRef.current?.focus();
  }, []);

  const select = useCallback(
    (code: string) => {
      if (code === AUTO) onAutoDetect?.();
      else onLocaleChange(code);
      close();
    },
    [close, onLocaleChange, onAutoDetect],
  );

  // Focus the active option on open; lock background scroll; trap Tab.
  useEffect(() => {
    if (!open) return;
    optionRefs.current[rovingIdx]?.focus();
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        close();
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
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- focus only on open
  }, [open, close]);

  const onOptionKey = (e: React.KeyboardEvent, idx: number) => {
    const move = (next: number) => {
      const clamped = (next + options.length) % options.length;
      setFocusIdx(clamped);
      optionRefs.current[clamped]?.focus();
    };
    if (e.key === "ArrowDown" || e.key === "ArrowRight") {
      e.preventDefault();
      move(idx + 1);
    } else if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
      e.preventDefault();
      move(idx - 1);
    } else if (e.key === "Home") {
      e.preventDefault();
      move(0);
    } else if (e.key === "End") {
      e.preventDefault();
      move(options.length - 1);
    } else if (/^[a-z]$/i.test(e.key)) {
      // Type-ahead: jump to the first option whose label starts with the
      // typed letter, matching native <select> and WAI-ARIA listbox behavior.
      const match = options.findIndex((o) => o.label.toLowerCase().startsWith(e.key.toLowerCase()));
      if (match >= 0) {
        e.preventDefault();
        move(match);
      }
    }
  };

  return (
    <div className="langsel">
      <button
        ref={btnRef}
        type="button"
        className="langsel-btn"
        aria-label={
          auto
            ? `Response language: Auto-detect (currently ${current.label})`
            : `Response language: ${current.label}`
        }
        aria-haspopup="dialog"
        aria-expanded={open}
        title={auto ? "Response language: auto-detected" : "Response language"}
        onClick={() => (open ? close() : openMenu())}
        onKeyDown={(e) => {
          if (!open && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
            e.preventDefault();
            openMenu();
          }
        }}
      >
        <GlobeIcon />
        <span>{current.value.toUpperCase()}</span>
        {auto && <span className="langsel-auto">auto</span>}
        <ChevronDownIcon />
      </button>

      {open && (
        <div
          className="lmv2-overlay"
          onMouseDown={(e) => e.target === e.currentTarget && close()}
        >
          <div
            ref={panelRef}
            className="lmv2"
            role="dialog"
            aria-modal="true"
            aria-label="Response language"
          >
            <div className="lmv2-head">
              <h2>Response language</h2>
              <button
                type="button"
                className="dlgv2-x lmv2-x"
                onClick={close}
                aria-label="Close language picker"
              >
                <CloseIcon />
              </button>
            </div>
            <div className="lmv2-list" role="radiogroup" aria-label={t('language.title')}>
              {options.map((o, i) => (
                <button
                  key={o.value}
                  ref={(el) => {
                    optionRefs.current[i] = el;
                  }}
                  type="button"
                  role="radio"
                  aria-checked={isChecked(o.value)}
                  className="lmv2-opt"
                  tabIndex={i === rovingIdx ? 0 : -1}
                  onKeyDown={(e) => onOptionKey(e, i)}
                  onClick={() => select(o.value)}
                >
                  <span className="lmv2-names">
                    <span className="lmv2-name">{o.label}</span>
                    {o.native && <span className="lmv2-native">{o.native}</span>}
                  </span>
                  {isChecked(o.value) && <CheckIcon />}
                </button>
              ))}
            </div>
            <div className="lmv2-foot">
              {onAutoDetect
                ? "Pick a language to always answer in it, or let answers follow the language you type."
                : "Answers and narration follow the selected language."}
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
