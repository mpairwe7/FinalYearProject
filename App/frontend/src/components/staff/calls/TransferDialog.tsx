'use client';

import React, { useEffect, useState } from 'react';
import { callsApi, type OfficerPresence } from '@/services/callsApi';
import { transferCall } from '@/services/officerCallSession';

export function TransferDialog({
  open,
  callId: _callId,
  onClose,
  onTransferred,
}: {
  open: boolean;
  callId: string;
  onClose: () => void;
  onTransferred?: () => void;
}) {
  const [teams, setTeams] = useState<string[]>([]);
  const [officers, setOfficers] = useState<OfficerPresence[]>([]);
  const [targetType, setTargetType] = useState<'team' | 'officer'>('team');
  const [selectedTeam, setSelectedTeam] = useState<string>('general');
  const [selectedOfficer, setSelectedOfficer] = useState<string>('');
  const [note, setNote] = useState<string>('');
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    setLoading(true);
    setError(null);
    callsApi
      .getPresence()
      .then((data) => {
        setTeams(data.teams || ['general', 'taxpayer_accounts', 'disputes', 'customs', 'registration']);
        if (data.teams?.length) setSelectedTeam(data.teams[0]);
        const avail = (data.officers || []).filter((o) => o.status === 'available');
        setOfficers(avail);
        if (avail.length > 0) setSelectedOfficer(avail[0].user_id);
      })
      .catch((err) => setError((err as Error).message || 'Failed to load teams'))
      .finally(() => setLoading(false));
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  if (!open) return null;

  const handleTransfer = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      await transferCall({
        team: targetType === 'team' ? selectedTeam : undefined,
        officer_id: targetType === 'officer' ? selectedOfficer : undefined,
        note: note.trim() || undefined,
      });
      onClose();
      onTransferred?.();
    } catch (err) {
      setError((err as Error).message || 'Transfer failed');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="cc-dialog-backdrop" role="presentation" onClick={onClose}>
      <div
        className="cc-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Transfer call"
        onClick={(e) => e.stopPropagation()}
      >
        <header className="cc-dialog-head">
          <h3>Transfer Call</h3>
          <button type="button" className="cc-btn cc-btn--quiet" onClick={onClose}>
            ✕
          </button>
        </header>

        <form onSubmit={handleTransfer} className="cc-dialog-body">
          {error && <div className="cc-dialog-error" role="alert">{error}</div>}

          <div className="cc-field-group">
            <label className="cc-label">Transfer destination</label>
            <div className="cc-radio-row">
              <label>
                <input
                  type="radio"
                  name="targetType"
                  value="team"
                  checked={targetType === 'team'}
                  onChange={() => setTargetType('team')}
                />{' '}
                Team
              </label>
              <label>
                <input
                  type="radio"
                  name="targetType"
                  value="officer"
                  checked={targetType === 'officer'}
                  onChange={() => setTargetType('officer')}
                />{' '}
                Specific Officer ({officers.length} available)
              </label>
            </div>
          </div>

          {targetType === 'team' ? (
            <div className="cc-field">
              <label htmlFor="team-select" className="cc-label">
                Department / Team
              </label>
              <select
                id="team-select"
                className="cc-select"
                value={selectedTeam}
                onChange={(e) => setSelectedTeam(e.target.value)}
                disabled={loading || submitting}
              >
                {teams.map((t) => (
                  <option key={t} value={t}>
                    {t.replace(/_/g, ' ')}
                  </option>
                ))}
              </select>
            </div>
          ) : (
            <div className="cc-field">
              <label htmlFor="officer-select" className="cc-label">
                Available Officer
              </label>
              {officers.length === 0 ? (
                <p className="cc-hint">No officers currently available. Transfer to a team queue instead.</p>
              ) : (
                <select
                  id="officer-select"
                  className="cc-select"
                  value={selectedOfficer}
                  onChange={(e) => setSelectedOfficer(e.target.value)}
                  disabled={loading || submitting}
                >
                  {officers.map((o) => (
                    <option key={o.user_id} value={o.user_id}>
                      {o.display_name || o.user_id}
                    </option>
                  ))}
                </select>
              )}
            </div>
          )}

          <div className="cc-field">
            <label htmlFor="transfer-note" className="cc-label">
              Transfer note (internal for next officer)
            </label>
            <textarea
              id="transfer-note"
              className="cc-textarea"
              rows={3}
              placeholder="Explain context or reason for transfer..."
              value={note}
              onChange={(e) => setNote(e.target.value)}
              disabled={submitting}
            />
          </div>

          <footer className="cc-dialog-actions">
            <button type="button" className="cc-btn cc-btn--quiet" onClick={onClose} disabled={submitting}>
              Cancel
            </button>
            <button
              type="submit"
              className="cc-btn cc-btn--primary"
              disabled={submitting || (targetType === 'officer' && !selectedOfficer)}
            >
              {submitting ? 'Transferring…' : 'Transfer call'}
            </button>
          </footer>
        </form>
      </div>
    </div>
  );
}
