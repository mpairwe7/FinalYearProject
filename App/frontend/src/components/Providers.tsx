"use client";

import { useEffect, useState, type ReactNode } from 'react';
import { onlineManager, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { useChatStore } from '@/store/useChatStore';
import { useVoiceStore } from '@/store/useVoiceStore';

function getErrorStatus(error: unknown): number | undefined {
  if (typeof error !== 'object' || error === null || !('status' in error)) return undefined;
  const status = (error as { status?: unknown }).status;
  return typeof status === 'number' ? status : undefined;
}

function shouldRetry(failureCount: number, error: unknown): boolean {
  const status = getErrorStatus(error);
  if (status && status >= 400 && status < 500 && status !== 408 && status !== 429) {
    return false;
  }
  return failureCount < 2;
}

function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        gcTime: 10 * 60_000,
        retry: shouldRetry,
        retryDelay: (attempt) => Math.min(1_000 * 2 ** attempt, 8_000),
        refetchOnWindowFocus: false,
        refetchOnReconnect: true,
        networkMode: 'online',
      },
      mutations: {
        retry: (failureCount, error) => shouldRetry(failureCount, error) && failureCount < 1,
        retryDelay: (attempt) => Math.min(1_000 * 2 ** attempt, 5_000),
        networkMode: 'online',
      },
    },
  });
}

export default function Providers({ children }: { children: ReactNode }) {
  const [queryClient] = useState(createQueryClient);

  useEffect(() => {
    useChatStore.getState().hydratePersisted();
    void useVoiceStore.persist.rehydrate();

    const syncOnlineState = () => {
      const online = navigator.onLine;
      useVoiceStore.getState().setOnline(online);
      onlineManager.setOnline(online);
    };

    syncOnlineState();
    window.addEventListener('online', syncOnlineState);
    window.addEventListener('offline', syncOnlineState);

    return () => {
      window.removeEventListener('online', syncOnlineState);
      window.removeEventListener('offline', syncOnlineState);
    };
  }, []);

  return (
    <QueryClientProvider client={queryClient}>
      {children}
    </QueryClientProvider>
  );
}
