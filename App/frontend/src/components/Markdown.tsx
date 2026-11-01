"use client";

import React, { lazy, memo, Suspense, useMemo } from "react";

import { stripCitationMarkers } from "../lib/answerText";
import { PHONE_SRC } from "../lib/uraContacts";

const MermaidDiagram = lazy(() => import("./MermaidDiagram"));

/**
 * Lightweight Markdown → React renderer (zero dependencies).
 *
 * Handles both structured markdown AND flat LLM prose by:
 *   - Parsing standard markdown (headings, lists, code, blockquotes, etc.)
 *   - Auto-splitting long flat paragraphs at sentence boundaries
 *   - Stripping [1] citation markers (references live in the Sources block)
 *   - Breaking on --- for disclaimer sections
 */

function isExternalHttp(href: string): boolean {
  return /^https?:\/\//i.test(href);
}

/**
 * Allowlist URL schemes for LLM/RAG-authored `[text](url)` links (output is
 * semi-untrusted). Permit http(s), mailto, tel, site-relative and in-page
 * anchors; reject javascript:, data:, vbscript:, protocol-relative `//`, etc.
 * Returns the original href when safe, or null so the caller renders plain text.
 */
function safeHref(href: string): string | null {
  const h = href.trim();
  // Strip whitespace + control chars before scheme-testing so obfuscated
  // schemes (with embedded tabs/newlines) cannot slip past the allowlist.
  const probe = Array.from(h).filter((ch) => ch.charCodeAt(0) > 32).join("");
  return /^(?:https?:\/\/|mailto:|tel:|\/(?!\/)|#)/i.test(probe) ? h : null;
}

/** Peel trailing sentence punctuation off an autolinked URL so it stays as text. */
function splitTrail(s: string): [string, string] {
  const m = s.match(/[.,;:!?)\]*]+$/);
  return m ? [s.slice(0, s.length - m[0].length), m[0]] : [s, ""];
}

// Inline matcher: bold+italic, bold (** or __), italic (* or _), code,
// [text](url), [n] citation, bare URL, email, phone, and scheme-less
// (*.?)ura.go.ug domains — tried left-to-right at each position, so
// `[text](url)` beats `[n]` (numeric link text stays a link) and a URL inside
// `(...)` is consumed as a unit (never double-linked).
const INLINE_SOURCE = [
  /(\*\*\*(.+?)\*\*\*)/.source, //                                        1,2    ***bold italic***
  /(\*\*(.+?)\*\*)/.source, //                                            3,4    **bold**
  /((?<![A-Za-z0-9])__([^_\n]+?)__(?![A-Za-z0-9]))/.source, //            5,6    __bold__
  /(\*(.+?)\*)/.source, //                                                7,8    *italic*
  /((?<![A-Za-z0-9])_([^_\n]+?)_(?![A-Za-z0-9]))/.source, //              9,10   _italic_
  /(`([^`]+)`)/.source, //                                                11,12  `code`
  /(\[([^\]]+)\]\(([^)]+)\))/.source, //                                  13,14,15 [text](url)
  /(\[(\d+)\])/.source, //                                                16,17  [n] citation
  /((?:https?:\/\/|www\.)[^\s<]+)/.source, //                             18     bare URL / www.
  /([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})/.source, //       19     email
  `((?<!\\d)(?:${PHONE_SRC})(?!\\d))`, //                                  20     phone
  /(\b(?:[a-z0-9-]+\.)*ura\.go\.ug(?:\/[^\s<]*)?)/.source, //             21     scheme-less (*.?)ura.go.ug
].join("|");

/**
 * Parse inline Markdown formatting (emphasis, inline code, links, citations,
 * URLs, emails, and URA phone numbers) into React nodes.
 */
function renderInline(text: string, depth = 0, inLink = false): React.ReactNode[] {
  if (!text || depth > 3) return [text];
  const nodes: React.ReactNode[] = [];
  let lastIdx = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  const inlineRe = new RegExp(INLINE_SOURCE, "gi");

  const pushLink = (href: string, label: React.ReactNode, external: boolean) => {
    nodes.push(
      <a
        key={key++}
        href={href}
        className="md-link"
        {...(external ? { target: "_blank", rel: "noopener noreferrer nofollow" } : {})}
      >
        {label}
      </a>,
    );
  };

  while ((match = inlineRe.exec(text)) !== null) {
    if (match.index > lastIdx) {
      nodes.push(text.slice(lastIdx, match.index));
    }
    if (match[2]) {
      nodes.push(
        <strong key={key++}>
          <em>{renderInline(match[2], depth + 1, inLink)}</em>
        </strong>,
      );
    } else if (match[4] || match[6]) {
      const inner = (match[4] ?? match[6])!;
      nodes.push(<strong key={key++}>{renderInline(inner, depth + 1, inLink)}</strong>);
    } else if (match[8] || match[10]) {
      const inner = (match[8] ?? match[10])!;
      nodes.push(<em key={key++}>{renderInline(inner, depth + 1, inLink)}</em>);
    } else if (match[12]) {
      nodes.push(
        <code key={key++} className="md-inline-code">
          {match[12]}
        </code>
      );
    } else if (match[14] && match[15]) {
      // [text](url) — sanitized; unsafe schemes degrade to plain styled text
      const href = safeHref(match[15]);
      const labelNodes = renderInline(match[14], depth + 1, Boolean(href) || inLink);
      if (href && !inLink) pushLink(href, labelNodes, isExternalHttp(href));
      else nodes.push(<span key={key++} className="md-link">{labelNodes}</span>);
    } else if (match[17]) {
      nodes.push(
        <sup key={key++} className="md-cite-ref">
          {match[17]}
        </sup>
      );
    } else if (inLink && (match[18] || match[19] || match[20] || match[21])) {
      nodes.push(match[0]);
    } else if (match[18]) {
      const [url, trail] = splitTrail(match[18]);
      const href = /^www\./i.test(url) ? `https://${url}` : url;
      pushLink(href, url, true);
      if (trail) nodes.push(trail);
    } else if (match[19]) {
      pushLink(`mailto:${match[19]}`, match[19], false);
    } else if (match[20]) {
      pushLink(`tel:${match[20].replace(/[\s-]/g, "")}`, match[20], false);
    } else if (match[21]) {
      const [dom, trail] = splitTrail(match[21]);
      pushLink(`https://${dom}`, dom, true);
      if (trail) nodes.push(trail);
    }
    lastIdx = match.index + match[0].length;
  }
  if (lastIdx < text.length) {
    nodes.push(text.slice(lastIdx));
  }
  return nodes.length > 0 ? nodes : [text];
}

const DIAGRAM_LANGS = new Set(["mermaid"]);

interface Block {
  type: "paragraph" | "heading" | "ul" | "ol" | "code" | "diagram" | "blockquote" | "hr" | "table" | "callout";
  level?: number;
  start?: number;
  items?: string[];
  lang?: string;
  text?: string;
  rows?: string[][];
  calloutKind?: string;
}

const TRAILING_ABBR_RE =
  /(?:^|[\s(])(?:e\.g|i\.e|etc|no|sec|art|cap|sch|para|reg|vol|ch|pt|shs|ushs|ugx|mr|mrs|ms|dr|st|ltd|inc|co|corp|vs|p\.o|s|ss)\.$/i;

/** Split prose into sentences without breaking after statutory or common abbreviations. */
function splitSentencesSafe(text: string): string[] {
  const rawParts = text.split(/(?<=[.!?])\s+/);
  const out: string[] = [];
  for (const part of rawParts) {
    if (!part) continue;
    if (out.length > 0 && TRAILING_ABBR_RE.test(out[out.length - 1])) {
      out[out.length - 1] += " " + part;
    } else {
      out.push(part);
    }
  }
  return out;
}

/**
 * Split a long flat paragraph into multiple shorter paragraphs
 * at sentence boundaries for readability.
 *
 * Only splits if the text is longer than ~200 chars and has
 * 3+ sentences. This preserves intentional short paragraphs
 * while breaking up LLM walls-of-text.
 */
function splitLongParagraph(text: string): string[] {
  if (text.length < 200 || asNumberedProcedure(text) !== null) {
    return [text];
  }

  // Split on sentence boundaries while keeping statutory/common abbreviations intact
  const sentences: string[] = [];
  let buf = '';
  const parts = splitSentencesSafe(text);

  for (const part of parts) {
    if (!buf) {
      buf = part;
    } else if (buf.length + part.length < 180) {
      buf += ' ' + part;
    } else {
      sentences.push(buf);
      buf = part;
    }
  }
  if (buf) sentences.push(buf);

  // There is no citation-marker case to handle here: stripCitationMarkers runs
  // before parsing, so a trailing "[1]" can no longer become its own sentence
  // and strand itself as a paragraph holding nothing but a pill.

  // Only split if we got multiple chunks
  return sentences.length >= 2 ? sentences : [text];
}

/**
 * Turn a "lead-in: a; b; c." enumeration into a lead-in plus list items.
 *
 * Retrieved answers arrive as the corpus wrote them, and the corpus writes
 * lists as prose. The URA services answer is one 600-character sentence
 * holding nine distinct services separated by semicolons — every word of it
 * useful, none of it scannable, and sentence-splitting cannot help because it
 * is a single sentence.
 *
 * This is a layout change only: the same words in the same order, with the
 * separators the author already put there used as the list boundaries. It is
 * deliberately conservative, because reformatting grounded text is only safe
 * while it stays lossless:
 *
 *   - needs a colon lead-in and 3+ semicolon-separated parts, so ordinary
 *     prose that happens to contain a semicolon is left alone;
 *   - every part must be short enough to read as an item, so a paragraph of
 *     semicolon-joined clauses is not shredded;
 *
 * Returns null when the text is not an enumeration, and the caller keeps its
 * paragraph.
 */
function asEnumeration(text: string): { lead: string; items: string[] } | null {
  if (text.includes("•") && !text.includes("`")) {
    const bulletParts = text
      .split("•")
      .map((p) => p.trim())
      .filter(Boolean);
    const hasLead = !text.trimStart().startsWith("•");
    const lead = hasLead ? (bulletParts.shift() ?? "").trim() : "";
    const items = bulletParts
      .map((p) => p.replace(/[.;]+$/, "").trim())
      .filter(Boolean);
    if (items.length >= 2 && items.every((p) => p.length <= 220)) {
      return { lead, items };
    }
  }
  // A lead-in is the common shape ("Core services include: a; b; c") but not a
  // required one — the corpus also stores bare lists that open straight into
  // the first item ("Pension; employer-paid medical expenses; …", "6%
  // goods/services to designated agents over UGX 1m; 6% resident management
  // fees; …"). Those are the same thing without the introduction, and skipping
  // them left the two longest walls of text in the corpus unformatted.
  //
  // Only the first colon on the FIRST segment counts as a lead-in. Items
  // frequently contain their own colons ("Port clearance (Mombasa/Dar):
  // consolidator's agent files WT8"), and splitting there would cut an item in
  // half and promote its tail to the introduction.
  const firstSegment = text.split(";")[0] ?? "";
  const colon = firstSegment.indexOf(":");

  let lead = "";
  let rest = text.trim();
  if (colon >= 0) {
    lead = text.slice(0, colon + 1);
    rest = text.slice(colon + 1).trim();
  }
  if (!rest || rest.length < 80) return null;

  rest = rest.replace(/\.$/, "");

  const parts = rest
    .split(";")
    .map((p) => p.trim())
    .filter(Boolean);
  if (parts.length < 3) return null;
  // An item longer than this is a clause, not a list entry. Measured against
  // the real corpus: the longest genuine item here is the 170-character
  // "domestic tax administration — VAT, PAYE and employment income, …", so a
  // tighter bound silently rejects the exact answer this exists to fix.
  if (parts.some((p) => p.length > 220)) return null;

  const items = parts.map((p, idx) =>
    // "and online payments…" reads as a leftover conjunction once the item
    // stands on its own line.
    idx === parts.length - 1 ? p.replace(/^and\s+/i, "") : p,
  );
  return { lead: lead.trim(), items };
}

/**
 * Split an inline numbered procedure into ordered-list items.
 *
 * The corpus writes procedures on one line — "1) Write an expression of
 * interest to Commissioner Customs. 2) Hold preliminary consultation with the
 * Customs AEO team. 3) Submit the self-assessment form…". The list parser only
 * recognises a marker at the start of a line, so the whole procedure became a
 * single list item several hundred characters long: numbered on screen, but
 * still a wall to read, and worse than a paragraph because it looked like it
 * had been formatted.
 *
 * Requires a run starting at 1) and ascending by one, so a sentence that
 * merely mentions "2)" cannot trigger it and a mis-numbered list is left
 * alone rather than silently renumbered.
 */
function asNumberedProcedure(text: string): { lead: string; items: string[] } | null {
  const marker = /(?:^|[\s:;.,!?])(\d{1,2})[).](?:\s+|(?=\*{1,2}[A-Za-z]|[A-Z]))/g;
  const found: { n: number; start: number; end: number }[] = [];
  let m: RegExpExecArray | null;
  while ((m = marker.exec(text)) !== null) {
    const digitMatch = m[0].match(/\d{1,2}/);
    const digitOffset = digitMatch?.index ?? 0;
    found.push({
      n: Number(m[1]),
      start: m.index + digitOffset,
      end: m.index + m[0].length,
    });
  }
  if (found.length < 2) return null;
  if (found[0].n !== 1) return null;
  for (let i = 1; i < found.length; i++) {
    if (found[i].n !== found[i - 1].n + 1) return null;
  }
  const lead = text.slice(0, found[0].start).trim();
  if (found.length === 2 && lead && !lead.endsWith(":")) return null;

  const items: string[] = [];
  for (let i = 0; i < found.length; i++) {
    const stop = i + 1 < found.length ? found[i + 1].start : text.length;
    let item = text.slice(found[i].end, stop).trim();
    if (i + 1 < found.length) {
      item = item.replace(/[.;,]+$/, "").trim();
    }
    if (!item) return null;
    if (found.length === 2 && item.length > 220) return null;
    items.push(item);
  }
  return { lead, items };
}

/**
 * Typographic clean-up for retrieved text.
 *
 * The corpus stores em dashes as a literal double hyphen ("domestic tax
 * administration -- VAT"), which renders as exactly that. Only the spaced
 * form is converted, so `--flag` in prose and the `---` horizontal rule are
 * both left alone.
 */
function tidyTypography(text: string): string {
  return text
    .replace(/(\s)--(\s)/g, "$1—$2")
    .replace(/\bCustomary Services\b/g, "Customs Services");
}

/** True when a line opens with an ATX heading marker (`#`..`####`). */
function isHeading(line: string) {
  return /^(#{1,4})\s+(.+)/.test(line);
}

const CALLOUT_LINE_RE =
  /^(?:\*{1,2}|_)?(note|important|tip|warning|caution|summary)(?:\*{1,2}|_)?:\s*(?:\*{1,2}|_)?\s*(.+)/i;

/** True when a line starts with a callout label such as `Note:` or `**Important:**`. */
function isCallout(line: string) {
  return CALLOUT_LINE_RE.test(line.trim());
}

/** True when a line starts with an unordered list marker (`-`, `*`, `+`, or `•`). */
function isUnorderedListItem(line: string) {
  return /^[-*+]\s+/.test(line.trimStart()) || /^\u2022\s+/.test(line.trimStart());
}

/** True when a line starts with an ordered list marker (`1.` or `1)`). */
function isOrderedListItem(line: string) {
  return /^\d+[\.)]\s+/.test(line.trimStart());
}

/** True when a line is a Markdown pipe-table separator row (`| --- | --- |`). */
function isTableSeparator(line: string) {
  return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
}

/** True when a line is a Markdown pipe-table data or header row. */
function isTableRow(line: string) {
  return line.includes("|") && !isTableSeparator(line);
}

/** Split a pipe-table row into trimmed cell strings. */
function parseTableRow(line: string) {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

/** Normalize smashed greetings, inline bullet runs, orphan first list items, and trailing sign-offs. */
function normalizeMarkdownSource(src: string): string {
  const rawLines = stripCitationMarkers(src).split("\n");
  const unsmashed: string[] = [];
  let inFence = false;

  for (let line of rawLines) {
    if (line.trimStart().startsWith("```")) {
      inFence = !inFence;
      unsmashed.push(line);
      continue;
    }
    if (!inFence && !line.includes("|") && !line.includes("`")) {
      line = line.replace(/,\.(?=\s|$|[A-Z"')\]])/g, ".");
      line = line.replace(/([a-zA-Z)\]])([!?])([A-Z])/g, "$1$2 $3");
      line = line.replace(/([a-z)\]])\.([A-Z][a-z])/g, "$1. $2");
      if (
        !/^\s*(?:[-*+•]|\d{1,2}[.)]|#{1,6})\s+/.test(line) &&
        /^\s*(?!(?:note|important|tip|warning|caution|summary|disclaimer)\b)\*{0,2}[A-Z][A-Za-z0-9 /&,'’()-]{1,45}?\*{0,2}:[ \t]+\S/i.test(
          line,
        ) &&
        /[a-z0-9\)]\.[ \t]*[-*•](?!\*)[ \t]*\*{0,2}[A-Z]/.test(line)
      ) {
        line = `- ${line.trimStart()}`;
      }
      line = line.replace(/([;:])[ \t]*[-*•](?!\*)[ \t]*(\*{0,2}[A-Za-z])/g, "$1\n- $2");
      line = line.replace(/([a-z0-9\)])\.[ \t]*[-*•](?!\*)[ \t]*(\*{0,2}[A-Za-z])/g, "$1.\n- $2");
      line = line.replace(/(?<=\S)[ \t]+•[ \t]+(\*{0,2}[A-Za-z0-9])/g, "\n- $1");
    }
    unsmashed.push(...line.split("\n"));
  }

  inFence = false;
  for (let idx = 0; idx < unsmashed.length; idx++) {
    let line = unsmashed[idx];
    if (line.trimStart().startsWith("```")) {
      inFence = !inFence;
      continue;
    }
    if (inFence || line.includes("|") || !line.trim()) continue;
    if (
      !/^\s*(?:[-*+•]|\d{1,2}[.)]|#{1,6})\s+/.test(line) &&
      /^\s*(?!(?:note|important|tip|warning|caution|summary|disclaimer)\b)\*{0,2}[A-Z][A-Za-z0-9 /&,'’()-]{1,45}?\*{0,2}:[ \t]+\S/i.test(
        line,
      )
    ) {
      let prev = "";
      for (let p = idx - 1; p >= 0; p--) {
        if (unsmashed[p].trim()) {
          prev = unsmashed[p].trim();
          break;
        }
      }
      let next = "";
      for (let n = idx + 1; n < unsmashed.length; n++) {
        if (unsmashed[n].trim()) {
          next = unsmashed[n].trim();
          break;
        }
      }
      if (prev.endsWith(":") && /^[-*+•]\s+/.test(next)) {
        line = `- ${line.trimStart()}`;
      }
    }
    line = line.replace(
      /^(\s*[-*+•]\s+)(?!\*\*)(?!(?:https?|mailto|tel):)([A-Z][A-Za-z0-9 /&,'’()-]{1,45}?):([ \t]+)/,
      "$1**$2**:$3",
    );
    if (/^\s*(?:[-*+•]|\d{1,2}[.)])\s+/.test(line)) {
      line = line.replace(
        /([.!?])\s+((?:Please\s+feel\s+free|Feel\s+free\s+to|Please\s+let\s+me\s+know|Let\s+me\s+know\s+if|If\s+you\s+need\s+(?:any\s+)?(?:further|more|additional)\s+(?:assistance|help|information|clarification)|If\s+you\s+have\s+any\s+(?:further|other|more)\s+questions|Do\s+not\s+hesitate\s+to\s+reach\s+out|Don['’]t\s+hesitate\s+to\s+reach\s+out|Should\s+you\s+need\s+any\s+further|Would\s+you\s+like\s+more\s+detail)\b.*)$/i,
        "$1\n\n$2",
      );
    }
    unsmashed[idx] = line;
  }

  return unsmashed.join("\n");
}

/** Parse a Markdown string into structured block descriptors. */
function parseBlocks(src: string): Block[] {
  const lines = normalizeMarkdownSource(src).split("\n");
  const blocks: Block[] = [];
  let i = 0;
  let nextOrderedStep = 1;
  let prevBlockWasSublist = false;

  while (i < lines.length) {
    const line = lines[i];

    // Code block
    if (line.startsWith("```")) {
      const lang = line.slice(3).trim();
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !lines[i].startsWith("```")) {
        codeLines.push(lines[i]);
        i++;
      }
      const blockType = DIAGRAM_LANGS.has(lang.toLowerCase()) ? "diagram" : "code";
      blocks.push({ type: blockType, text: codeLines.join("\n"), lang });
      nextOrderedStep = 1;
      prevBlockWasSublist = false;
      i++;
      continue;
    }

    // HR
    if (/^-{3,}$/.test(line.trim())) {
      blocks.push({ type: "hr" });
      nextOrderedStep = 1;
      prevBlockWasSublist = false;
      i++;
      continue;
    }

    // Pipe table
    if (isTableRow(line) && i + 1 < lines.length && isTableSeparator(lines[i + 1])) {
      const rows: string[][] = [parseTableRow(line)];
      i += 2;
      while (i < lines.length && isTableRow(lines[i])) {
        rows.push(parseTableRow(lines[i]));
        i++;
      }
      blocks.push({ type: "table", rows });
      nextOrderedStep = 1;
      prevBlockWasSublist = false;
      continue;
    }

    // Heading
    const hMatch = line.match(/^(#{1,4})\s+(.+)/);
    if (hMatch) {
      blocks.push({ type: "heading", level: hMatch[1].length, text: hMatch[2] });
      nextOrderedStep = 1;
      prevBlockWasSublist = false;
      i++;
      continue;
    }

    // Callout labels commonly produced by LLMs (plain or bolded, e.g. Note: or **Important:**)
    const calloutMatch = line.trim().match(CALLOUT_LINE_RE);
    if (calloutMatch) {
      blocks.push({
        type: "callout",
        calloutKind: calloutMatch[1].toLowerCase(),
        text: calloutMatch[2].replace(/(?:\*{1,2}|_)$/, "").trim(),
      });
      nextOrderedStep = 1;
      prevBlockWasSublist = false;
      i++;
      continue;
    }

    // Blockquote
    if (line.startsWith("> ")) {
      const qLines: string[] = [];
      while (i < lines.length && lines[i].startsWith("> ")) {
        qLines.push(lines[i].slice(2));
        i++;
      }
      blocks.push({ type: "blockquote", text: qLines.join("\n") });
      nextOrderedStep = 1;
      prevBlockWasSublist = false;
      continue;
    }

    // Unordered list
    if (isUnorderedListItem(line)) {
      const firstUlIndent = line.length - line.trimStart().length;
      const items: string[] = [];
      while (i < lines.length) {
        if (isUnorderedListItem(lines[i])) {
          items.push(lines[i].trimStart().replace(/^([-*+]|\u2022)\s+/, ""));
          i++;
        } else if (!lines[i].trim()) {
          let next = i + 1;
          while (next < lines.length && !lines[next].trim()) next++;
          if (next < lines.length && isUnorderedListItem(lines[next])) {
            i = next;
          } else {
            break;
          }
        } else {
          break;
        }
      }
      const prevBlock = blocks.length > 0 ? blocks[blocks.length - 1] : undefined;
      const lastOlItem =
        prevBlock?.type === "ol" && prevBlock.items?.length
          ? prevBlock.items[prevBlock.items.length - 1]
          : "";
      const isSubordinateUl =
        prevBlock?.type === "ol" &&
        (firstUlIndent > 0 || lastOlItem.trim().replace(/[*_]+$/g, "").endsWith(":"));
      blocks.push({ type: "ul", items });
      if (isSubordinateUl) {
        prevBlockWasSublist = true;
      } else {
        nextOrderedStep = 1;
        prevBlockWasSublist = false;
      }
      continue;
    }

    // Ordered list — supports both tight lists and loose lists with blank lines
    if (isOrderedListItem(line)) {
      const items: string[] = [];
      while (i < lines.length) {
        if (isOrderedListItem(lines[i])) {
          items.push(tidyTypography(lines[i].trimStart()));
          i++;
        } else if (!lines[i].trim()) {
          let next = i + 1;
          while (next < lines.length && !lines[next].trim()) next++;
          if (next < lines.length && isOrderedListItem(lines[next])) {
            i = next;
          } else {
            break;
          }
        } else {
          break;
        }
      }
      const firstNumMatch = items[0]?.match(/^(\d+)[.)]\s+/);
      const rawStartNum = firstNumMatch ? Number(firstNumMatch[1]) : 1;
      const effectiveStart =
        rawStartNum > 1 ? rawStartNum : prevBlockWasSublist && nextOrderedStep > 1 ? nextOrderedStep : 1;
      const expanded = items.flatMap((it) => asNumberedProcedure(it)?.items ?? [it]);
      const cleanedItems = expanded.map((it) => it.replace(/^\d+[.)]\s+/, ""));
      blocks.push({
        type: "ol",
        start: effectiveStart > 1 ? effectiveStart : undefined,
        items: cleanedItems,
      });
      nextOrderedStep = effectiveStart + cleanedItems.length;
      prevBlockWasSublist = false;
      continue;
    }

    // Empty line
    if (!line.trim()) {
      i++;
      continue;
    }

    // Paragraph — collect consecutive non-empty, non-special lines
    const pLines: string[] = [line];
    i++;
    while (
      i < lines.length &&
      lines[i].trim() &&
      !lines[i].startsWith("```") &&
      !isHeading(lines[i]) &&
      !lines[i].startsWith("> ") &&
      !isCallout(lines[i]) &&
      !isUnorderedListItem(lines[i]) &&
      !isOrderedListItem(lines[i]) &&
      !(isTableRow(lines[i]) && i + 1 < lines.length && isTableSeparator(lines[i + 1])) &&
      !/^-{3,}$/.test(lines[i].trim())
    ) {
      pLines.push(lines[i]);
      i++;
    }

    // Join into a single paragraph text, then auto-split if too long
    const fullText = tidyTypography(pLines.join(" "));
    const chunks = splitLongParagraph(fullText);
    for (const chunk of chunks) {
      // A prose enumeration becomes a lead-in plus a real list; anything else
      // stays the paragraph it was.
      const procedure = asNumberedProcedure(chunk);
      if (procedure) {
        if (procedure.lead) blocks.push({ type: "paragraph", text: procedure.lead });
        blocks.push({ type: "ol", items: procedure.items });
        nextOrderedStep = procedure.items.length + 1;
        prevBlockWasSublist = false;
        continue;
      }
      const enumerated = asEnumeration(chunk);
      if (enumerated) {
        if (enumerated.lead) blocks.push({ type: "paragraph", text: enumerated.lead });
        blocks.push({ type: "ul", items: enumerated.items });
        nextOrderedStep = 1;
        prevBlockWasSublist = false;
      } else {
        blocks.push({ type: "paragraph", text: chunk });
        nextOrderedStep = 1;
        prevBlockWasSublist = false;
      }
    }
  }

  return blocks;
}

/** Render parsed Markdown blocks into React elements. */
function renderBlocks(blocks: Block[]): React.ReactNode[] {
  return blocks.map((block, i) => {
    switch (block.type) {
      case "hr":
        return <hr key={i} className="md-hr" />;

      case "heading": {
        const level = Math.min(Math.max(block.level ?? 2, 1), 4) as 1 | 2 | 3 | 4;
        const Tag = `h${level}` as "h1" | "h2" | "h3" | "h4";
        return (
          <Tag key={i} className={`md-h${level}`}>
            {renderInline(block.text!)}
          </Tag>
        );
      }

      case "diagram":
        return (
          <Suspense
            key={i}
            fallback={<div className="md-diagram-container md-diagram-loading">Rendering diagram...</div>}
          >
            <MermaidDiagram content={block.text!} />
          </Suspense>
        );

      case "code":
        return (
          <pre key={i} className="md-code-block">
            <code>{block.text!.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")}</code>
          </pre>
        );

      case "blockquote":
        return (
          <blockquote key={i} className="md-blockquote">
            {renderInline(block.text!)}
          </blockquote>
        );

      case "callout":
        return (
          <aside key={i} className={`md-callout md-callout-${block.calloutKind}`}>
            <strong>{block.calloutKind}</strong>
            <p className="md-callout-text">{renderInline(block.text!)}</p>
          </aside>
        );

      case "table": {
        const [head = [], ...body] = block.rows ?? [];
        return (
          <div key={i} className="md-table-wrap">
            <table className="md-table">
              <thead>
                <tr>
                  {head.map((cell, j) => (
                    <th key={j}>{renderInline(cell)}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {body.map((row, rowIndex) => (
                  <tr key={rowIndex}>
                    {head.map((_, cellIndex) => (
                      <td key={cellIndex}>{renderInline(row[cellIndex] ?? "")}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        );
      }

      case "ul":
        return (
          <ul key={i} className="md-list">
            {block.items!.map((item, j) => (
              <li key={j}>{renderInline(item)}</li>
            ))}
          </ul>
        );

      case "ol":
        return (
          <ol key={i} className="md-list md-ol" start={block.start}>
            {block.items!.map((item, j) => (
              <li key={j}>{renderInline(item)}</li>
            ))}
          </ol>
        );

      case "paragraph":
      default:
        return (
          <p key={i} className="md-p">
            {renderInline(block.text!)}
          </p>
        );
    }
  });
}

function MarkdownInner({ content }: { content: string }) {
  const rendered = useMemo(() => {
    const blocks = parseBlocks(content);
    return renderBlocks(blocks);
  }, [content]);

  return <div className="md-body">{rendered}</div>;
}

const Markdown = memo(MarkdownInner);
export default Markdown;
