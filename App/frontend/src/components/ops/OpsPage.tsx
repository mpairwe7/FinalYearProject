"use client";

import React from "react";

/**
 * The one page shell every operations screen renders into.
 *
 * Before this, four staff pages each wrote their own header: `.ov-head`,
 * `.ag-head`, `.tickets-header` and `.analytics-header`, with four title sizes
 * (23 / 22 / 22 / 28px), four paddings and four content widths. Nothing about
 * those differences was intentional. One component means a title block, a
 * description, an actions slot and a toolbar slot behave identically wherever
 * you land, and the width of the console stops depending on which link you
 * clicked.
 *
 * `id="staff-main"` is the skip-link target the shared nav points at, so it
 * belongs to the shell rather than being repeated per page.
 */
export function OpsPage({
  eyebrow,
  title,
  description,
  actions,
  toolbar,
  children,
  width = "wide",
  className = "",
}: {
  /** Section this page belongs to — the console's breadcrumb of last resort. */
  eyebrow?: React.ReactNode;
  title: string;
  description?: React.ReactNode;
  /** Right side of the title row: period pickers, refresh, primary action. */
  actions?: React.ReactNode;
  /** One filter row, above everything it scopes. */
  toolbar?: React.ReactNode;
  children: React.ReactNode;
  /** "read" narrows to a comfortable measure for text-heavy consoles. */
  width?: "wide" | "read";
  className?: string;
}) {
  return (
    <main
      className={`ops-page${width === "read" ? " is-narrow" : ""}${className ? ` ${className}` : ""}`}
      id="staff-main"
    >
      <header className="ops-page-head">
        <div>
          {eyebrow ? <span className="ops-eyebrow">{eyebrow}</span> : null}
          <h1>{title}</h1>
          {description ? <p className="ops-page-desc">{description}</p> : null}
        </div>
        {actions ? <div className="ops-page-actions">{actions}</div> : null}
      </header>
      {toolbar ? <div className="ops-toolbar">{toolbar}</div> : null}
      {children}
    </main>
  );
}

export function OpsPanel({
  title,
  id,
  end,
  note,
  flush,
  bare,
  glass,
  children,
  className = "",
}: {
  title: React.ReactNode;
  /** Heading id, so the section can be `aria-labelledby` it. */
  id?: string;
  end?: React.ReactNode;
  note?: React.ReactNode;
  /** Tighter padding for a panel whose body is a list of rows. */
  flush?: boolean;
  /**
   * Drop the card — no surface, border or radius — and sit the section directly
   * on the page, its heading and content aligned to the page gutter like the
   * page title above them. A rule under the heading and a wider gap below do
   * the separating a border used to.
   *
   * For a section that is the page's own content rather than an object on it.
   * A panel that has to be told apart from its neighbours *at a glance* — a
   * table beside a chart, one of a row of equals — should stay a card.
   */
  bare?: boolean;
  /**
   * The opposite: lift the panel off the page on the console's glass material,
   * with a deeper shadow behind it. For the one section on a page that is acted
   * *in* rather than read — a composer above the list it writes into. At most
   * one per page; two things floating is nothing floating.
   */
  glass?: boolean;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      className={`ops-panel${bare ? " is-bare" : ""}${glass ? " is-glass" : ""}${className ? ` ${className}` : ""}`}
      aria-labelledby={id}
    >
      <div className="ops-panel-head">
        <h2 id={id}>{title}</h2>
        {end ? <div className="ops-panel-end">{end}</div> : null}
      </div>
      <div className={`ops-panel-body${flush ? " is-flush" : ""}`}>
        {note ? <p className="ops-panel-note">{note}</p> : null}
        {children}
      </div>
    </section>
  );
}

/**
 * A horizontally scrolling table, reachable from the keyboard.
 *
 * A `overflow-x: auto` box that only a pointer can scroll strands its right-hand
 * columns for anyone without one — axe's `scrollable-region-focusable`, which is
 * how the analytics feedback table was caught at a phone width while the flags
 * and outbox tables (which happened to carry the attributes inline) passed. One
 * component means the next table cannot forget.
 */
export function TableScroll({
  label,
  children,
}: {
  /** Names the region — what the table is about. */
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="ops-table-wrap" tabIndex={0} role="region" aria-label={label}>
      {children}
    </div>
  );
}
