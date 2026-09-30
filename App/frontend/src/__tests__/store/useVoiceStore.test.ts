/**
 * useVoiceStore — what an upgrade does to settings already saved in a browser.
 */
import { describe, expect, it } from "vitest";
import { DEFAULT_SILENCE_TIMEOUT_MS, useVoiceStore } from "../../store/useVoiceStore";

const migrate = (persisted: Record<string, unknown>, version: number) =>
  useVoiceStore.persist.getOptions().migrate?.(persisted, version) as Record<string, unknown>;

describe("useVoiceStore persistence", () => {
  it("replaces the old, never-chosen 2000 ms pause with the new default", () => {
    // Before v3 nothing read or set silenceTimeout: 2000 is the old default, not a choice.
    const state = migrate({ silenceTimeout: 2000, voiceByLocale: { lg: "waxal_lug_0004" } }, 2);
    expect(state.silenceTimeout).toBe(DEFAULT_SILENCE_TIMEOUT_MS);
    expect(state.voiceByLocale).toEqual({ lg: "waxal_lug_0004" });
  });

  it("still carries a v1 English voice forward, and nothing else", () => {
    const state = migrate({ voiceId: "en-GB-SoniaNeural", silenceTimeout: 2000 }, 1);
    expect(state.voiceByLocale).toEqual({ en: "en-GB-SoniaNeural" });
    expect(state).not.toHaveProperty("voiceId");
    expect(state.silenceTimeout).toBe(DEFAULT_SILENCE_TIMEOUT_MS);
  });

  it("keeps a pause chosen under v3", () => {
    expect(migrate({ silenceTimeout: 2000, voiceByLocale: {} }, 3).silenceTimeout).toBe(2000);
    expect(migrate({ silenceTimeout: 0, voiceByLocale: {} }, 3).silenceTimeout).toBe(0);
  });

  it("starts a new browser at 1.2 s", () => {
    expect(DEFAULT_SILENCE_TIMEOUT_MS).toBe(1200);
    expect(useVoiceStore.getInitialState().silenceTimeout).toBe(1200);
  });
});
