import { useSyncExternalStore } from 'react';

import { narrationSnapshot, subscribeNarration, type NarrationSnapshot } from '../services/voiceService';

const NOTHING_PLAYING: NarrationSnapshot = { active: false, paused: false, sentence: '' };

/** What is being read aloud right now: whether it is paused, and the sentence being heard. */
export function useNarration(): NarrationSnapshot {
  return useSyncExternalStore(subscribeNarration, narrationSnapshot, () => NOTHING_PLAYING);
}
