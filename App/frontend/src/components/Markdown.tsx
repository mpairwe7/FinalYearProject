"use client";

import React, { lazy, memo, Suspense, useMemo } from "react";

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

function renderInline(text: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  const pattern =
    /(\*\*(.+?)\*\*)|(\*(.+?)\*)|(`([^`]+)`)|\[(\d+)\]|\[([^\]]+)\]\(([^)]+)\)/g;
  let lastIdx = 0;
  let match: RegExpExecArray | null;
  let key = 0;

  while ((match = pattern.exec(text)) !== null) {
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
    } else if (match[7]) {
      nodes.push(
        <sup key={key++} className="md-cite-ref">
          {match[7]}
        </sup>
      );
    } else if (match[8] && match[9]) {
      const href = match[9];
      const isSafe = /^https?:\/\//i.test(href);
      nodes.push(
        isSafe ? (
          <a key={key++} href={href} target="_blank" rel="noopener noreferrer" className="md-link">
            {match[8]}
          </a>
        ) : (
          <span key={key++} className="md-link">{match[8]}</span>
        ),
      );
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

  // Only split if we got multiple chunks
  return sentences.length >= 2 ? sentences : [text];
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
        items.push(lines[i].trimStart().replace(/^\d+[\.)]\s+/, ""));
        i++;
      }
      blocks.push({ type: "ol", items });
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
    const fullText = pLines.join(" ");
    const chunks = splitLongParagraph(fullText);
    for (const chunk of chunks) {
      blocks.push({ type: "paragraph", text: chunk });
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
