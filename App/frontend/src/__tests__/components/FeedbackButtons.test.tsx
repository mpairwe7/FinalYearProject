/**
 * A taxpayer's comment on an answer is not lost silently (G653). The comment is
 * how a wrong answer is contested; a failed save left the box sitting there
 * and the complaint gone, with nothing said.
 */
import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import FeedbackButtons from '../../components/FeedbackButtons';
import * as analytics from '../../store/useAnalyticsStore';

afterEach(() => vi.restoreAllMocks());

describe('FeedbackButtons comment', () => {
  it('says when the comment could not be saved, and keeps it', async () => {
    vi.spyOn(analytics, 'submitFeedback').mockResolvedValue(true as never);
    vi.spyOn(analytics, 'updateFeedbackComment').mockResolvedValue(false);
    render(<FeedbackButtons messageId="m1" userQuery="q" botReply="a" />);
    fireEvent.click(screen.getByRole('button', { name: 'Unhelpful response' }));
    const box = await screen.findByRole('textbox', { name: /Feedback comment/i });
    fireEvent.change(box, { target: { value: 'The VAT threshold changed in July.' } });
    fireEvent.click(screen.getByRole('button', { name: /^Send$/ }));
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent(/comment was not saved/i));
    expect((screen.getByRole('textbox', { name: /Feedback comment/i }) as HTMLTextAreaElement).value).toBe(
      'The VAT threshold changed in July.',
    );
  });
});
