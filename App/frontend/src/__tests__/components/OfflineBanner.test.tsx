import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen } from '@testing-library/react';
import OfflineBanner from '../../components/OfflineBanner';
import * as networkHook from '../../hooks/useNetworkStatus';
import { useChatStore } from '../../store/useChatStore';
import { lg } from '../../lib/i18n/lg';

describe('OfflineBanner', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it('renders offline warning when network is offline', () => {
    vi.spyOn(networkHook, 'useNetworkStatus').mockReturnValue({
      isOnline: false,
      isOffline: true,
      isLowBandwidth: false,
      effectiveType: 'unknown',
      saveData: false,
      downlink: null,
      rtt: null,
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

  // G618: the offline page has no calculator, so the banner must not promise one,
  // and it speaks the taxpayer's language like the rest of the chat.
  const offline = () =>
    vi.spyOn(networkHook, 'useNetworkStatus').mockReturnValue({
      isOnline: false,
      isOffline: true,
      isLowBandwidth: false,
      effectiveType: 'unknown',
      saveData: false,
      downlink: null,
      rtt: null,
    });

  it('does not promise a calculator offline', () => {
    useChatStore.getState().setLocale('en');
    offline();
    render(<OfflineBanner />);
    expect(screen.getByRole('status').textContent).not.toMatch(/calculat/i);
    expect(screen.getByRole('link').getAttribute('href')).toBe('/offline.html');
  });

  it('is in the taxpayer\'s language', () => {
    useChatStore.getState().setLocale('lg');
    offline();
    render(<OfflineBanner />);
    const status = screen.getByRole('status').textContent ?? '';
    expect(status).toContain(lg['common.offline_notice'] as string);
    expect(status).not.toMatch(/You are offline/);
    useChatStore.getState().setLocale('en');
  });
});
