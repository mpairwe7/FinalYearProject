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

  it('renders nothing when network is online and normal bandwidth', () => {
    vi.spyOn(networkHook, 'useNetworkStatus').mockReturnValue({
      isOnline: true,
      isOffline: false,
      isLowBandwidth: false,
      effectiveType: '4g',
      saveData: false,
      downlink: 10,
      rtt: 50,
    });

    const { container } = render(<OfflineBanner />);
    expect(container.firstChild).toBeNull();
  });

  it('renders low bandwidth warning when online with isLowBandwidth', () => {
    vi.spyOn(networkHook, 'useNetworkStatus').mockReturnValue({
      isOnline: true,
      isOffline: false,
      isLowBandwidth: true,
      effectiveType: '2g',
      saveData: false,
      downlink: 0.2,
      rtt: 1500,
    });

    render(<OfflineBanner />);
    expect(screen.getByRole('status')).toBeDefined();
    expect(screen.getByText(/Low Bandwidth/i)).toBeDefined();
  });
});
