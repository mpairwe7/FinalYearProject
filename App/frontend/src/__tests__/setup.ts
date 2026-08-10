/**
 * Vitest global setup — extends matchers and provides browser API stubs.
 *
 * Aligned with ISO/IEC 25010:2023 §7 (Maintainability) — tests run
 * identically across local dev, CI, and Docker environments.
 */
import "@testing-library/jest-dom/vitest";

// Stub crypto.randomUUID for JSDOM (used by useChatStore.generateId)
if (!globalThis.crypto?.randomUUID) {
  Object.defineProperty(globalThis, "crypto", {
    value: {
      ...globalThis.crypto,
      randomUUID: () =>
        "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
          const r = (Math.random() * 16) | 0;
          return (c === "x" ? r : (r & 0x3) | 0x8).toString(16);
        }),
    },
  });
}

// Stub sessionStorage/localStorage for Zustand persist
const storageMock = (() => {
  let store: Record<string, string> = {};
  return {
    getItem: (key: string) => store[key] ?? null,
    setItem: (key: string, value: string) => {
      store[key] = value;
    },
    removeItem: (key: string) => {
      delete store[key];
    },
    clear: () => {
      store = {};
    },
    get length() {
      return Object.keys(store).length;
    },
    key: (i: number) => Object.keys(store)[i] ?? null,
  };
})();

Object.defineProperty(globalThis, "localStorage", { value: storageMock });
Object.defineProperty(globalThis, "sessionStorage", { value: storageMock });

// Stub navigator.sendBeacon (analytics)
Object.defineProperty(globalThis.navigator, "sendBeacon", {
  value: () => true,
  writable: true,
});

// Stub matchMedia (dark mode / responsive queries)
Object.defineProperty(globalThis, "matchMedia", {
  value: (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: () => {},
    removeListener: () => {},
    addEventListener: () => {},
    removeEventListener: () => {},
    dispatchEvent: () => false,
  }),
});
