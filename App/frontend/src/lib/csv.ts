/**
 * CSV evidence files from the console's lists (the audit trail, the quality
 * audit), one place for the escaping rules and the download.
 *
 * A spreadsheet treats a cell starting with = + - @ (or a tab / carriage
 * return) as a formula, so an exported value like "=HYPERLINK(...)" would run
 * when an auditor opens the file. Such cells are prefixed with a quote
 * (OWASP CSV injection guidance); every cell is quoted and inner quotes
 * doubled. Rows end in CRLF, as RFC 4180 has it.
 */

export type CsvValue = string | number | boolean | null | undefined;

export function csvCell(value: CsvValue): string {
  const text = value == null ? "" : String(value);
  const safe = /^[=+\-@\t\r]/.test(text) ? `'${text}` : text;
  return `"${safe.replaceAll('"', '""')}"`;
}

/** A whole file: the header row, then one row per item. */
export function toCsv(header: readonly string[], rows: readonly CsvValue[][]): string {
  return [header, ...rows].map((row) => row.map(csvCell).join(",")).join("\r\n") + "\r\n";
}

/** "2026-10-13T09:00:00.000Z" for an epoch-seconds time; blank for none. */
export function csvTime(ts: number | null | undefined): string {
  return ts ? new Date(ts * 1000).toISOString() : "";
}

/** Save *csv* as a file. Plain browser download; the console is not sandboxed. */
export function downloadCsv(csv: string, filename: string): void {
  const url = URL.createObjectURL(new Blob([csv], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

/** "quality-needs-review-2026-10-13.csv" */
export function csvFilename(stem: string, now: Date = new Date()): string {
  return `${stem}-${now.toISOString().slice(0, 10)}.csv`;
}
