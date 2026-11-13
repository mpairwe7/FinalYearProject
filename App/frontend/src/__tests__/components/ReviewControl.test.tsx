/**
 * Reopening an auditor's decision asks first, and earlier decisions stay
 * visible (G639). Reopen deleted the finding and its note in one click.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { ReviewControl } from '../../components/quality/ReviewControl';
import { analyticsApi, type NeedsReviewItem } from '../../services/analyticsApi';

const item = (over: Partial<NeedsReviewItem> = {}): NeedsReviewItem => ({
  item_type: 'rating', item_id: 'r-1', stream: 'officer_call', title: 'Officer call rated 2', quote: '', comment: '',
  topic: '', locale: 'en', officer_name: '', occurred_at: 1790000000, link: { tab: 'calls', param: 'id', value: 'r-1' },
  review: null, ...over,
} as NeedsReviewItem);

function renderControl(it: NeedsReviewItem) {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ReviewControl item={it} />
    </QueryClientProvider>,
  );
}

afterEach(() => vi.restoreAllMocks());

describe('ReviewControl', () => {
  it('asks before reopening, and says the decision stays in the history', () => {
    const set = vi.spyOn(analyticsApi, 'setQualityReview').mockResolvedValue({} as never);
    renderControl(item({ review: { status: 'action_needed', note: 'Wrong PAYE band', reviewer_name: 'Auditor A', updated_at: 1790000000 } }));
    fireEvent.click(screen.getByRole('button', { name: 'Reopen' }));
    expect(set).not.toHaveBeenCalled();
    const confirm = screen.getByRole('alertdialog', { name: /Reopen this item/ });
    expect(confirm).toHaveTextContent(/stays in the history/i);
    fireEvent.click(screen.getByRole('button', { name: 'Keep the decision' }));
    expect(screen.queryByRole('alertdialog')).toBeNull();
    expect(set).not.toHaveBeenCalled();
  });

  it('shows what was decided before an item was reopened', () => {
    renderControl(
      item({
        history: [
          { status: 'action_needed', note: 'Wrong PAYE band', reviewer_name: 'Auditor A', decided_at: 1790000000 },
          { status: 'open', note: '', reviewer_name: 'Auditor B', decided_at: 1790000100 },
        ],
      }),
    );
    const earlier = screen.getByRole('list', { name: /Earlier decisions/ });
    expect(earlier).toHaveTextContent('Action needed');
    expect(earlier).toHaveTextContent('Wrong PAYE band');
    expect(earlier).toHaveTextContent(/Reopened by Auditor B/);
  });

  // CodeRabbit on PR 730: once an item was decided again, the finding and the
  // reopening before it could be read only by reopening the item.
  it('keeps the earlier decisions readable on an item decided again', () => {
    renderControl(
      item({
        review: { status: 'reviewed', note: 'Fine on second look', reviewer_name: 'Auditor C', updated_at: 1790000200 },
        history: [
          { status: 'action_needed', note: 'Wrong PAYE band', reviewer_name: 'Auditor A', decided_at: 1790000000 },
          { status: 'open', note: '', reviewer_name: 'Auditor B', decided_at: 1790000100 },
          { status: 'reviewed', note: 'Fine on second look', reviewer_name: 'Auditor C', decided_at: 1790000200 },
        ],
      }),
    );
    fireEvent.click(screen.getByText(/Earlier decisions \(2\)/));
    const earlier = screen.getByRole('list', { name: /Earlier decisions/ });
    expect(earlier).toHaveTextContent('Wrong PAYE band');
    expect(earlier).toHaveTextContent(/Reopened by Auditor B/);
    // The current decision is shown above, not repeated in the history.
    expect(earlier).not.toHaveTextContent('Fine on second look');
  });

  it('shows no history control for an item decided once', () => {
    renderControl(
      item({
        review: { status: 'reviewed', note: '', reviewer_name: 'Auditor C', updated_at: 1790000200 },
        history: [{ status: 'reviewed', note: '', reviewer_name: 'Auditor C', decided_at: 1790000200 }],
      }),
    );
    expect(screen.queryByText(/Earlier decisions/)).toBeNull();
  });
});
