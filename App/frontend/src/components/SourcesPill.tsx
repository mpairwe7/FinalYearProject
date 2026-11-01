import React, { memo, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react';
import type { Citation, ContextResource } from '../store/useChatStore';
import { citationHref, sourceLabel } from '../lib/uraContacts';
import { safeHttpUrl } from '../lib/safeUrl';
import { useTranslation, type TranslationKey } from '../lib/i18n';
import { BookIcon, DownloadIcon, ExternalLinkIcon, FileIcon, GlobeIcon, ScaleIcon } from './Icons';

/**
 * Everything an answer rests on, folded into one pill at its end.
 *
 * An answer used to close with a grid of form cards ("Official URA Forms,
 * Templates & Sources") and, separately, a "Sources (N)" disclosure for the
 * passages it quoted. Together they were often taller than the answer, and
 * they sat between the reader and the follow-up chips. The links are still
 * one gesture away — hover, tap, or Enter on the pill — but no longer take
 * the space of a second answer.
 */

type SourceKind = 'online' | 'download' | 'statute' | 'guide' | 'passage';

export interface SourceItem {
  key: string;
  kind: SourceKind;
  title: string;
  /** Only http(s) links are kept; anything else renders as plain text. */
  href: string | null;
  meta: string[];
  detail?: string;
  tooltip: string;
}

const KIND_LABEL: Record<Exclude<SourceKind, 'passage'>, TranslationKey> = {
  online: 'resource.type.online',
  download: 'resource.type.download',
  statute: 'resource.type.statute',
  guide: 'resource.type.guide',
};

const KIND_ACTION: Record<Exclude<SourceKind, 'passage'>, TranslationKey> = {
  online: 'resource.action.online',
  download: 'resource.action.download',
  statute: 'resource.action.statute',
  guide: 'resource.action.guide',
};

/** Forms the taxpayer can act on are listed before what the answer quoted. */
const FORM_KINDS: ReadonlySet<SourceKind> = new Set(['online', 'download']);

const OPEN_DELAY_MS = 120;
const CLOSE_DELAY_MS = 180;
const PANEL_MAX_HEIGHT = 320;
const PANEL_MIN_HEIGHT = 160;
const PANEL_WIDTH = 360;
const VIEWPORT_EDGE = 16;
const GAP = 8;
/** The floating top strip the transcript scrolls under. */
const TOP_INSET = 64;
const DEFAULT_DOCK_HEIGHT = 92;

function resourceKind(type: string | undefined): SourceKind {
  if (type === 'online_form') return 'online';
  if (type === 'downloadable_form') return 'download';
  if (type === 'statutory_source') return 'statute';
  return 'guide';
}

function dedupeKey(href: string): string {
  return href.replace(/#.*$/, '').replace(/\/+$/, '').toLowerCase();
}

function present(parts: Array<string | undefined | null>): string[] {
  return parts.map((p) => (p ?? '').trim()).filter(Boolean);
}

/**
 * One list from the turn's forms and its quoted passages, de-duplicated.
 *
 * A passage without its own URL is keyed by document, page and section rather
 * than by link: `citationHref` sends every one of them to the portal home
 * page, and keying on that would collapse distinct passages into one row.
 */
export function collectSources(
  citations: Citation[] | undefined,
  resources: ContextResource[] | undefined,
  label: (key: TranslationKey, vars?: Record<string, string | number>) => string,
): SourceItem[] {
  const items: SourceItem[] = [];
  const seen = new Set<string>();

  for (const res of resources ?? []) {
    const title = typeof res?.title === 'string' ? res.title.trim() : '';
    if (!title) continue;
    const href = safeHttpUrl(res.url);
    const key = href ? dedupeKey(href) : `title:${title.toLowerCase()}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const kind = resourceKind(res.type);
    const format = typeof res.format === 'string' && res.format && res.format !== 'web' ? `.${res.format.toLowerCase()}` : '';
    items.push({
      key,
      kind,
      title,
      href,
      meta: present([label(KIND_LABEL[kind as Exclude<SourceKind, 'passage'>]), res.source_domain, format, res.effective_year, res.citation]),
      detail: res.description?.trim() || undefined,
      tooltip: `${label(KIND_ACTION[kind as Exclude<SourceKind, 'passage'>])}: ${title}`,
    });
  }

  for (const c of citations ?? []) {
    const explicit = safeHttpUrl(c.url);
    const key = explicit ? dedupeKey(explicit) : `cite:${c.source}|${c.page ?? ''}|${c.section ?? ''}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const passage = c.passage?.trim();
    items.push({
      key,
      kind: 'passage',
      title: c.title?.trim() || sourceLabel(c.source),
      href: citationHref(c),
      meta: present([
        c.effective_date,
        // The publisher's date. A crawl date is never shown: it made old pages look current.
        c.published_at ? label('sources.published', { date: c.published_at }) : '',
        c.page ? `p. ${c.page}` : '',
        c.section,
      ]),
      detail: passage ? (passage.length > 160 ? `${passage.slice(0, 160)}…` : passage) : undefined,
      tooltip: explicit || `${c.source} — official URA documents`,
    });
  }

  return items;
}

function KindIcon({ kind }: { kind: SourceKind }) {
  if (kind === 'online') return <GlobeIcon />;
  if (kind === 'download') return <DownloadIcon />;
  if (kind === 'statute') return <ScaleIcon />;
  if (kind === 'guide') return <BookIcon />;
  return <FileIcon />;
}

function SourceRow({ item }: { item: SourceItem }) {
  const body = (
    <>
      <span className={`sources-row-icon is-${item.kind}`} aria-hidden="true">
        <KindIcon kind={item.kind} />
      </span>
      <span className="sources-row-text">
        <span className="sources-row-title">{item.title}</span>
        {item.meta.length > 0 && <span className="sources-row-meta">{item.meta.join(' · ')}</span>}
        {item.detail && <span className="sources-row-detail">{item.detail}</span>}
      </span>
      {item.href && (
        <span className="sources-row-go" aria-hidden="true">
          <ExternalLinkIcon size={12} />
        </span>
      )}
    </>
  );
  return item.href ? (
    <a className="sources-row" href={item.href} target="_blank" rel="noopener noreferrer nofollow" title={item.tooltip}>
      {body}
    </a>
  ) : (
    <div className="sources-row is-static" title={item.tooltip}>
      {body}
    </div>
  );
}

interface PanelLayout {
  placement: 'up' | 'down';
  maxHeight: number;
  width: number;
  shift: number;
}

const INITIAL_LAYOUT: PanelLayout = { placement: 'up', maxHeight: PANEL_MAX_HEIGHT, width: PANEL_WIDTH, shift: 0 };

/**
 * Where the list fits: above the pill unless the transcript has no room there
 * (the first answer, scrolled to the top), and never past the composer dock or
 * the viewport's right edge on a phone.
 */
function measureLayout(button: HTMLElement): PanelLayout {
  const rect = button.getBoundingClientRect();
  const scroller = button.closest('.message-list');
  const bounds = scroller?.getBoundingClientRect();
  const dock = scroller ? parseFloat(getComputedStyle(scroller).getPropertyValue('--chat-dock-height')) : NaN;
  const top = (bounds?.top ?? 0) + TOP_INSET;
  const bottom = (bounds?.bottom ?? window.innerHeight) - (Number.isFinite(dock) ? dock : DEFAULT_DOCK_HEIGHT);
  const above = rect.top - top - GAP;
  const below = bottom - rect.bottom - GAP;
  const placement = above >= PANEL_MIN_HEIGHT || above >= below ? 'up' : 'down';
  const room = placement === 'up' ? above : below;
  const width = Math.min(PANEL_WIDTH, window.innerWidth - 2 * VIEWPORT_EDGE);
  const overflow = rect.left + width - (window.innerWidth - VIEWPORT_EDGE);
  return {
    placement,
    maxHeight: Math.max(PANEL_MIN_HEIGHT, Math.min(PANEL_MAX_HEIGHT, room)),
    width,
    shift: overflow > 0 ? -overflow : 0,
  };
}

interface SourcesPillProps {
  citations?: Citation[];
  resources?: ContextResource[];
}

function SourcesPillInner({ citations, resources }: SourcesPillProps) {
  const t = useTranslation();
  const items = useMemo(() => collectSources(citations, resources, t), [citations, resources, t]);
  const kinds = useMemo(() => [...new Set(items.map((item) => item.kind))].slice(0, 3), [items]);
  const groups = useMemo(() => {
    const forms = items.filter((item) => FORM_KINDS.has(item.kind));
    const references = items.filter((item) => !FORM_KINDS.has(item.kind));
    return [
      { id: 'forms', titleKey: 'sources.groupForms' as TranslationKey, items: forms },
      { id: 'references', titleKey: 'sources.groupReferences' as TranslationKey, items: references },
    ].filter((group) => group.items.length > 0);
  }, [items]);

  const [open, setOpen] = useState(false);
  // Opened by a click, tap or key: stays open until dismissed. Opened by
  // hovering: closes again when the pointer leaves.
  const [pinned, setPinned] = useState(false);
  const [layout, setLayout] = useState<PanelLayout>(INITIAL_LAYOUT);
  const wrapRef = useRef<HTMLSpanElement>(null);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const timerRef = useRef<number | null>(null);
  const panelId = useId();

  const clearTimer = useCallback(() => {
    if (timerRef.current != null) {
      window.clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const close = useCallback(
    (returnFocus = false) => {
      clearTimer();
      setOpen(false);
      setPinned(false);
      if (returnFocus) buttonRef.current?.focus();
    },
    [clearTimer],
  );

  useEffect(() => clearTimer, [clearTimer]);

  useLayoutEffect(() => {
    if (open && buttonRef.current) setLayout(measureLayout(buttonRef.current));
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!wrapRef.current?.contains(event.target as Node)) close();
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') close(true);
    };
    document.addEventListener('pointerdown', onPointerDown);
    document.addEventListener('keydown', onKeyDown);
    return () => {
      document.removeEventListener('pointerdown', onPointerDown);
      document.removeEventListener('keydown', onKeyDown);
    };
  }, [open, close]);

  const onPointerEnter = useCallback(
    (event: React.PointerEvent) => {
      if (event.pointerType !== 'mouse') return;
      clearTimer();
      if (!open) timerRef.current = window.setTimeout(() => setOpen(true), OPEN_DELAY_MS);
    },
    [clearTimer, open],
  );

  const onPointerLeave = useCallback(
    (event: React.PointerEvent) => {
      if (event.pointerType !== 'mouse') return;
      clearTimer();
      if (!pinned) timerRef.current = window.setTimeout(() => setOpen(false), CLOSE_DELAY_MS);
    },
    [clearTimer, pinned],
  );

  const onToggle = useCallback(() => {
    clearTimer();
    if (open && !pinned) {
      setPinned(true);
    } else if (open) {
      close();
    } else {
      setOpen(true);
      setPinned(true);
    }
  }, [clearTimer, close, open, pinned]);

  const onBlur = useCallback(
    (event: React.FocusEvent) => {
      if (open && !wrapRef.current?.contains(event.relatedTarget as Node | null)) close();
    },
    [close, open],
  );

  if (items.length === 0) return null;

  return (
    <span
      ref={wrapRef}
      className={`sources-pill-wrap${open ? ` is-open is-${layout.placement}` : ''}`}
      onPointerEnter={onPointerEnter}
      onPointerLeave={onPointerLeave}
      onBlur={onBlur}
    >
      <button
        ref={buttonRef}
        type="button"
        className="sources-pill"
        aria-expanded={open}
        aria-controls={open ? panelId : undefined}
        aria-label={t('message.sources', { count: items.length })}
        onClick={onToggle}
        data-testid="sources-pill"
      >
        <span className="sources-pill-icons" aria-hidden="true">
          {kinds.map((kind) => (
            <span key={kind} className={`sources-pill-icon is-${kind}`}>
              <KindIcon kind={kind} />
            </span>
          ))}
        </span>
        <span className="sources-pill-label">{t('sources.label')}</span>
        <span className="sources-pill-count" aria-hidden="true">
          {items.length}
        </span>
      </button>
      {open && (
        <div
          id={panelId}
          className={`sources-popover is-${layout.placement}`}
          role="region"
          aria-label={t('message.sources', { count: items.length })}
          style={{ maxHeight: layout.maxHeight, width: layout.width, left: layout.shift }}
          data-testid="sources-popover"
        >
          {groups.map((group) => (
            <section key={group.id} className="sources-group">
              {groups.length > 1 && <h4 className="sources-group-title">{t(group.titleKey)}</h4>}
              <ul className="sources-list">
                {group.items.map((item) => (
                  <li key={item.key}>
                    <SourceRow item={item} />
                  </li>
                ))}
              </ul>
            </section>
          ))}
        </div>
      )}
    </span>
  );
}

export const SourcesPill = memo(SourcesPillInner);
export default SourcesPill;
