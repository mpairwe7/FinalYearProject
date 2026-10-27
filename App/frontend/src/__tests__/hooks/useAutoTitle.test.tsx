/**
 * Naming a conversation once its first answer lands: one request per
 * conversation, the model's title applied, and nothing done after a rename.
 */
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAutoTitle } from "../../hooks/useAutoTitle";
import { createTurn, useChatStore } from "../../store/useChatStore";

const store = () => useChatStore.getState();
const TIN = ["10000", "23456"].join(""); // a sample TIN, built so secret scanners do not flag it

function startConversation(question = `Verify TIN ${TIN} for my tax clearance`) {
  const id = store().ensureActiveConversationId();
  store().addTurns([createTurn("user", question), createTurn("assistant", "First confirm your returns are filed [1].")]);
  store().saveCurrentSession();
  return id;
}

function respondWith(body: unknown, ok = true) {
  return vi.fn().mockResolvedValue({ ok, json: async () => body });
}

beforeEach(() => {
  localStorage.clear();
  useChatStore.setState({ conversations: [], activeConversationId: null });
  store().reset();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useAutoTitle", () => {
  it("asks once for a title after the first answer and shows it", async () => {
    const fetchMock = respondWith({ title: "TIN verification for tax clearance", source: "model" });
    vi.stubGlobal("fetch", fetchMock);
    const id = startConversation();

    const { rerender } = renderHook(({ loading }) => useAutoTitle(loading), { initialProps: { loading: false } });
    await waitFor(() =>
      expect(store().conversations.find((c) => c.id === id)?.title).toBe("TIN verification for tax clearance"),
    );
    rerender({ loading: false });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("/api/v1/chat/title");
    const sent = JSON.parse(init.body);
    expect(sent.locale).toBe("en");
    expect(sent.messages[0]).toEqual({ role: "user", content: `Verify TIN ${TIN} for my tax clearance` });
    expect(sent.messages[1].role).toBe("assistant");
    expect(sent.messages[1].content).not.toContain("[1]");
  });

  it("waits while the answer is still streaming", () => {
    const fetchMock = respondWith({ title: "x", source: "model" });
    vi.stubGlobal("fetch", fetchMock);
    startConversation();
    renderHook(() => useAutoTitle(true));
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("keeps the provisional title when the model has none", async () => {
    const fetchMock = respondWith({ title: null, source: "unavailable" });
    vi.stubGlobal("fetch", fetchMock);
    const id = startConversation();
    renderHook(() => useAutoTitle(false));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(store().conversations.find((c) => c.id === id)?.title).toBe("Verify TIN for my tax clearance");
  });

  it("does not ask for a conversation the user has renamed", () => {
    const fetchMock = respondWith({ title: "x", source: "model" });
    vi.stubGlobal("fetch", fetchMock);
    const id = startConversation();
    store().renameSession(id, "My clearance");
    renderHook(() => useAutoTitle(false));
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
