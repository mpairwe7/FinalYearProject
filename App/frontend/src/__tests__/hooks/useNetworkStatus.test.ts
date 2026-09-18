import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useNetworkStatus } from "../../hooks/useNetworkStatus";

describe("useNetworkStatus", () => {
  const originalOnLine = navigator.onLine;

  beforeEach(() => {
    Object.defineProperty(navigator, "onLine", {
      writable: true,
      value: true,
    });
  });

  afterEach(() => {
    Object.defineProperty(navigator, "onLine", {
      writable: true,
      value: originalOnLine,
    });
    vi.restoreAllMocks();
  });

  it("reports online status initially", () => {
    const { result } = renderHook(() => useNetworkStatus());
    expect(result.current.isOnline).toBe(true);
    expect(result.current.isOffline).toBe(false);
  });

  it("updates when window triggers offline event", () => {
    const { result } = renderHook(() => useNetworkStatus());
    act(() => {
      Object.defineProperty(navigator, "onLine", { value: false });
      window.dispatchEvent(new Event("offline"));
    });
    expect(result.current.isOnline).toBe(false);
    expect(result.current.isOffline).toBe(true);
  });

  it("updates when window triggers online event", () => {
    Object.defineProperty(navigator, "onLine", { value: false });
    const { result } = renderHook(() => useNetworkStatus());
    expect(result.current.isOnline).toBe(false);

    act(() => {
      Object.defineProperty(navigator, "onLine", { value: true });
      window.dispatchEvent(new Event("online"));
    });
    expect(result.current.isOnline).toBe(true);
    expect(result.current.isOffline).toBe(false);
  });

  it("detects low bandwidth when effectiveType is 2g", () => {
    const mockConnection = Object.assign(new EventTarget(), {
      effectiveType: "2g",
      saveData: false,
      downlink: 0.25,
      rtt: 1500,
    });

    Object.defineProperty(navigator, "connection", {
      writable: true,
      configurable: true,
      value: mockConnection,
    });

    const { result } = renderHook(() => useNetworkStatus());
    expect(result.current.effectiveType).toBe("2g");
    expect(result.current.isLowBandwidth).toBe(true);
    expect(result.current.downlink).toBe(0.25);
    expect(result.current.rtt).toBe(1500);
  });

  it("detects low bandwidth when saveData is true", () => {
    const mockConnection = Object.assign(new EventTarget(), {
      effectiveType: "4g",
      saveData: true,
      downlink: 5,
      rtt: 100,
    });

    Object.defineProperty(navigator, "connection", {
      writable: true,
      configurable: true,
      value: mockConnection,
    });

    const { result } = renderHook(() => useNetworkStatus());
    expect(result.current.saveData).toBe(true);
    expect(result.current.isLowBandwidth).toBe(true);
  });
});
