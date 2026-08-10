'use client';

import { useEffect } from 'react';

/**
 * Registers the hand-rolled service worker (/sw.js) for PWA offline support.
 * Render once in the root layout — it has no visual output.
 */
export default function ServiceWorkerRegistrar() {
  useEffect(() => {
    if (typeof window === 'undefined' || !('serviceWorker' in navigator)) return;

    navigator.serviceWorker
      .register('/sw.js')
      .then((reg) => {
        // Auto-apply waiting SW on update
        reg.addEventListener('updatefound', () => {
          const newSW = reg.installing;
          if (!newSW) return;
          newSW.addEventListener('statechange', () => {
            if (newSW.state === 'activated' && navigator.serviceWorker.controller) {
              // New version ready — the next navigation will use it
              console.info('[SW] Updated to new version');
            }
          });
        });
      })
      .catch((err) => console.warn('[SW] Registration failed:', err));
  }, []);

  return null;
}
