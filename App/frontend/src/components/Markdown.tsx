"use client";

import React, { lazy, memo, Suspense, useMemo } from "react";

import { PHONE_SRC } from "../lib/uraContacts";

const MermaidDiagram = lazy(() => import("./MermaidDiagram"));

/**
 * Lightweight Markdown → React renderer (zero dependencies).
 *
 * Handles both structured markdown AND flat LLM prose by:
 *   - Parsing standard markdown (headings, lists, code, blockquotes, etc.)
 *   - Auto-splitting long flat paragraphs at sentence boundaries
 *   - Rendering [1] citation references as superscript pills
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
  const m = s.match(/[.,;:!?)\]]+$/);
  return m ? [s.slice(0, s.length - m[0].length), m[0]] : [s, ""];
}

// Inline matcher: bold, italic, code, [text](url), [n] citation, bare URL,
// email, phone, and the scheme-less ura.go.ug domain — tried left-to-right at
// each position, so `[text](url)` beats `[n]` (numeric link text stays a link)
// and a URL inside `(...)` is consumed as a unit (never double-linked).
const INLINE_RE = new RegExp(
  [
    /(\*\*(.+?)\*\*)/.source, //                              1,2  **bold**
    /(\*(.+?)\*)/.source, //                                  3,4  *italic*
    /(`([^`]+)`)/.source, //                                  5,6  `code`
    /(\[([^\]]+)\]\(([^)]+)\))/.source, //                    7,8,9  [text](url)
    /(\[(\d+)\])/.source, //                                  10,11  [n] citation
    /((?:https?:\/\/|www\.)[^\s<]+)/.source, //               12   bare URL / www.
    /([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})/.source, // 13  email
    `((?<!\\d)(?:${PHONE_SRC})(?!\\d))`, //                    14   phone
    /(\bura\.go\.ug(?:\/[^\s<]*)?)/.source, //                15   scheme-less ura.go.ug
  ].join("|"),
  "gi",
);

function renderInline(text: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  let lastIdx = 0;
  let match: RegExpExecArray | null;
  let key = 0;
  INLINE_RE.lastIndex = 0;

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

  while ((match = INLINE_RE.exec(text)) !== null) {
    if (match.index > lastIdx) {
      nodes.push(text.slice(lastIdx, match.index));
    }
    if (match[2]) {
      nodes.push(<strong key={key++}>{match[2]}</strong>);
    } else if (match[4]) {
      nodes.push(<em key={key++}>{match[4]}</em>);
    } else if (match[6]) {
      nodes.push(
        <code key={key++} className="md-inline-code">
          {match[6]}
        </code>
      );
    } else if (match[8] && match[9]) {
      // [text](url) — sanitized; unsafe schemes degrade to plain styled text
      const href = safeHref(match[9]);
      if (href) pushLink(href, match[8], isExternalHttp(href));
      else nodes.push(<span key={key++} className="md-link">{match[8]}</span>);
    } else if (match[11]) {
      nodes.push(
        <sup key={key++} className="md-cite-ref">
          {match[11]}
        </sup>
      );
    } else if (match[12]) {
      const [url, trail] = splitTrail(match[12]);
      const href = /^www\./i.test(url) ? `https://${url}` : url;
      pushLink(href, url, true);
      if (trail) nodes.push(trail);
    } else if (match[13]) {
      pushLink(`mailto:${match[13]}`, match[13], false);
    } else if (match[14]) {
      pushLink(`tel:${match[14].replace(/[\s-]/g, "")}`, match[14], false);
    } else if (match[15]) {
      const [dom, trail] = splitTrail(match[15]);
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
  items?: string[];
  lang?: string;
  text?: string;
  rows?: string[][];
  calloutKind?: string;
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
  if (text.length < 200) return [text];

  // Split on sentence boundaries: period/question/exclamation followed by space + capital
  const sentences: string[] = [];
  let buf = '';
  const parts = text.split(/(?<=[.!?])\s+/);

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

  // A trailing "[1]" is its own "sentence" after the split above, which left it
  // stranded as a paragraph containing nothing but a citation pill — a bare
  // superscript 1 floating under the answer. Reattach any citation-only chunk
  // to the text it refers to.
  for (let j = sentences.length - 1; j > 0; j--) {
    if (/^(?:\[\d+\]\s*)+$/.test(sentences[j].trim())) {
      sentences[j - 1] = `${sentences[j - 1]} ${sentences[j].trim()}`;
      sentences.splice(j, 1);
    }
  }

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
 *   - a trailing citation marker stays with the lead-in, where it refers to
 *     the whole answer rather than to the last item.
 *
 * Returns null when the text is not an enumeration, and the caller keeps its
 * paragraph.
 */
function asEnumeration(text: string): { lead: string; items: string[] } | null {
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

  // Keep a trailing "[1]" (and any run of them) attached to the lead-in.
  const cite = rest.match(/(\s*(?:\[\d+\]\s*)+)$/);
  if (cite) {
    rest = rest.slice(0, rest.length - cite[1].length).trim();
    lead += cite[1].replace(/\s+$/, "");
  }
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
function asNumberedProcedure(text: string): string[] | null {
  const marker = /(?:^|\s)(\d{1,2})[).]\s+/g;
  const found: { n: number; start: number; end: number }[] = [];
  let m: RegExpExecArray | null;
  while ((m = marker.exec(text)) !== null) {
    found.push({ n: Number(m[1]), start: m.index, end: m.index + m[0].length });
  }
  if (found.length < 3) return null;
  if (found[0].n !== 1) return null;
  for (let i = 1; i < found.length; i++) {
    if (found[i].n !== found[i - 1].n + 1) return null;
  }
  // Text before "1)" is a lead-in, not an item; this only handles the case
  // where the paragraph IS the procedure.
  if (text.slice(0, found[0].start).trim()) return null;

  const items: string[] = [];
  for (let i = 0; i < found.length; i++) {
    const stop = i + 1 < found.length ? found[i + 1].start : text.length;
    const item = text.slice(found[i].end, stop).trim().replace(/[.;]$/, "");
    if (!item) return null;
    items.push(item);
  }
  return items;
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
  return text.replace(/(\s)--(\s)/g, "$1—$2");
}

function isHeading(line: string) {
  return /^(#{1,4})\s+(.+)/.test(line);
}

function isCallout(line: string) {
  return /^(note|important|tip|warning|caution|summary):\s+.+/i.test(line.trim());
}

function isUnorderedListItem(line: string) {
  return /^[-*+]\s+/.test(line.trimStart()) || /^\u2022\s+/.test(line.trimStart());
}

function isOrderedListItem(line: string) {
  return /^\d+[\.)]\s+/.test(line.trimStart());
}

function isTableSeparator(line: string) {
  return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
}

function isTableRow(line: string) {
  return line.includes("|") && !isTableSeparator(line);
}

function parseTableRow(line: string) {
  return line
    .trim()
    .replace(/^\|/, "")
    .replace(/\|$/, "")
    .split("|")
    .map((cell) => cell.trim());
}

function parseBlocks(src: string): Block[] {
  const lines = src.split("\n");
  const blocks: Block[] = [];
  let i = 0;

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
      i++;
      continue;
    }

    // HR
    if (/^-{3,}$/.test(line.trim())) {
      blocks.push({ type: "hr" });
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
      continue;
    }

    // Heading
    const hMatch = line.match(/^(#{1,4})\s+(.+)/);
    if (hMatch) {
      blocks.push({ type: "heading", level: hMatch[1].length, text: hMatch[2] });
      i++;
      continue;
    }

    // Callout labels commonly produced by LLMs
    const calloutMatch = line.trim().match(/^(note|important|tip|warning|caution|summary):\s+(.+)/i);
    if (calloutMatch) {
      blocks.push({
        type: "callout",
        calloutKind: calloutMatch[1].toLowerCase(),
        text: calloutMatch[2],
      });
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
      continue;
    }

    // Unordered list
    if (isUnorderedListItem(line)) {
      const items: string[] = [];
      while (i < lines.length && isUnorderedListItem(lines[i])) {
        items.push(lines[i].trimStart().replace(/^([-*+]|\u2022)\s+/, ""));
        i++;
      }
      blocks.push({ type: "ul", items });
      continue;
    }

    // Ordered list
    if (isOrderedListItem(line)) {
      const items: string[] = [];
      while (i < lines.length && isOrderedListItem(lines[i])) {
        items.push(tidyTypography(lines[i].trimStart()));
        i++;
      }
      // A whole procedure written on one line ("1) … 2) … 3) …") matches the
      // marker test once and would otherwise become a single several-hundred
      // character item: numbered on screen, still a wall to read, and worse
      // than a paragraph because it looks like it was formatted. Expand it,
      // then strip the leading marker from whatever remains.
      const expanded = items.flatMap((it) => asNumberedProcedure(it) ?? [it]);
      blocks.push({
        type: "ol",
        items: expanded.map((it) => it.replace(/^\d+[.)]\s+/, "")),
      });
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
        blocks.push({ type: "ol", items: procedure });
        continue;
      }
      const enumerated = asEnumeration(chunk);
      if (enumerated) {
        if (enumerated.lead) blocks.push({ type: "paragraph", text: enumerated.lead });
        blocks.push({ type: "ul", items: enumerated.items });
      } else {
        blocks.push({ type: "paragraph", text: chunk });
      }
    }
  }

  return blocks;
}

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
            <span>{renderInline(block.text!)}</span>
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
          <ol key={i} className="md-list md-ol">
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
