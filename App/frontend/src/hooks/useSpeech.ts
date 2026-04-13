"use client";

import { useQuery, useMutation } from '@tanstack/react-query';
import {
  checkSpeechHealth,
  synthesize,
  translate,
  voiceChat,
  type SpeechHealthStatus,
  type SynthesizeResult,
  type TranslateResult,
  type VoiceChatResult,
} from '../services/voiceService';

// ---------------------------------------------------------------------------
// Speech health (cached, auto-refreshes every 60s)
// ---------------------------------------------------------------------------

export function useSpeechHealth() {
  return useQuery<SpeechHealthStatus>({
    queryKey: ['speechHealth'],
    queryFn: checkSpeechHealth,
    staleTime: 60_000,
    gcTime: 5 * 60_000,
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  });
}

// ---------------------------------------------------------------------------
// TTS mutation (fire-and-forget, deduplicates by text+locale)
// ---------------------------------------------------------------------------

export function useTtsMutation() {
  return useMutation<SynthesizeResult, Error, { text: string; language: string; voice?: string }>({
    mutationFn: ({ text, language, voice }) => synthesize(text, language, voice),
  });
}

// ---------------------------------------------------------------------------
// Translation mutation
// ---------------------------------------------------------------------------

export function useTranslateMutation() {
  return useMutation<TranslateResult, Error, { text: string; sourceLang: string; targetLang: string }>({
    mutationFn: ({ text, sourceLang, targetLang }) => translate(text, sourceLang, targetLang),
  });
}

// ---------------------------------------------------------------------------
// Voice chat mutation (compound pipeline)
// ---------------------------------------------------------------------------

export function useVoiceChatMutation() {
  return useMutation<
    VoiceChatResult,
    Error,
    {
      pcm16: ArrayBuffer;
      language?: string;
      ttsEnabled?: boolean;
      sessionId?: string;
    }
  >({
    mutationFn: ({ pcm16, ...opts }) => voiceChat(pcm16, opts),
  });
}
