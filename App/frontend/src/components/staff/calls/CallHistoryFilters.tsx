'use client';

import React, { useEffect, useRef, useState } from 'react';
import type { ListCallsParams } from '@/services/callsApi';
import { isTypingTarget } from '@/lib/ticketUi';

export function CallHistoryFilters({
  filters,
  onChange,
}: {
  filters: ListCallsParams;
  onChange: (next: ListCallsParams) => void;
}) {
  const [query, setQuery] = useState(filters.q || '');
  const searchInputRef = useRef<HTMLInputElement>(null);

  // Debounce search query 300ms
  useEffect(() => {
    const handler = setTimeout(() => {
      if (query !== (filters.q || '')) {
        onChange({ ...filters, q: query.trim() || undefined, offset: 0 });
      }
    }, 300);
    return () => clearTimeout(handler);
  }, [query, filters, onChange]);

  // Shortcut "/" focuses search
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === '/' && !isTypingTarget(e.target)) {
        e.preventDefault();
        searchInputRef.current?.focus();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  return (
    <div className="ch-filters" role="search" aria-label="Call history filters">
      <div className="ch-search-field">
        <input
          ref={searchInputRef}
          type="search"
          className="cc-input ch-search-input"
          placeholder="Search transcript or summary (press / to focus)…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
      </div>

      <div className="ch-select-group">
        <select
          className="cc-select"
          value={filters.outcome || ''}
          onChange={(e) => onChange({ ...filters, outcome: e.target.value || undefined, offset: 0 })}
          aria-label="Filter by outcome"
        >
          <option value="">All outcomes</option>
          <option value="resolved">Resolved</option>
          <option value="follow_up">Follow-up needed</option>
          <option value="callback">Callback</option>
          <option value="referred">Referred</option>
          <option value="abandoned">Abandoned</option>
        </select>

        <select
          className="cc-select"
          value={filters.language || ''}
          onChange={(e) => onChange({ ...filters, language: e.target.value || undefined, offset: 0 })}
          aria-label="Filter by language"
        >
          <option value="">All languages</option>
          <option value="en">English</option>
          <option value="lg">Luganda</option>
          <option value="sw">Swahili</option>
        </select>

        <select
          className="cc-select"
          value={filters.sort || 'started_desc'}
          onChange={(e) => onChange({ ...filters, sort: e.target.value, offset: 0 })}
          aria-label="Sort calls"
        >
          <option value="started_desc">Newest first</option>
          <option value="started_asc">Oldest first</option>
          <option value="duration_desc">Longest duration</option>
        </select>

        <label className="ch-checkbox-label">
          <input
            type="checkbox"
            checked={Boolean(filters.has_ticket)}
            onChange={(e) => onChange({ ...filters, has_ticket: e.target.checked ? true : undefined, offset: 0 })}
          />{' '}
          Has ticket
        </label>
      </div>
    </div>
  );
}
