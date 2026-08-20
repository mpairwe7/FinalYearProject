import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import OfflineBanner from '../../components/OfflineBanner';
import * as networkHook from '../../hooks/useNetworkStatus';

describe('OfflineBanner', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders offline warning when network is offline', () => {
    vi.spyOn(networkHook, 'useNetworkStatus').mockReturnValue({
      isOnline: false,
      isOffline: true,
    });

    render(<OfflineBanner />);
    expect(screen.getByRole('status')).toBeDefined();
    expect(screen.getByText(/You are offline/i)).toBeDefined();
  });

  it('renders nothing when network is online', () => {
    vi.spyOn(networkHook, 'useNetworkStatus').mockReturnValue({
      isOnline: true,
      isOffline: false,
    });

    const { container } = render(<OfflineBanner />);
    expect(container.firstChild).toBeNull();
  });
});
