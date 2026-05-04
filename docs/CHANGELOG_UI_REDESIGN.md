# URA Chatbot — UI Redesign & Backend Improvements Changelog

> **Session:** 2026-04-20 to 2026-04-21
> **Branch:** `fix/port-config-and-csp`

---

## Summary

Complete UI/UX redesign of the URA Tax Assistant chatbot, inspired by Grok's 2026 design language. Applied the same design patterns to the AgriForge companion project. Fixed critical backend issues including chain-of-thought leakage, speech service initialization, and API routing.

---

## Frontend Changes

### Design System Overhaul (`globals.css`)
- **Palette:** Replaced violet/cyan Grok palette with official URA colors (Navy `#003087`, Gold `#F9C74F`, Teal `#00A88F`)
- **Tokens:** 90+ CSS custom properties for colors, gradients, shadows, radii, blur
- **Components:** Added styles for voice modal, voice settings, consent banner, conversation rail, markdown renderer (all previously unstyled)
- **Accessibility:** WCAG 2.2 AA/AAA — gold focus rings, 44px touch targets, `prefers-reduced-motion`, pinch-to-zoom enabled

### Layout Redesign (`page.tsx`)
- **Landing state:** Centered URA logo (140px) + title + composer + 2x2 prompt grid (Grok-inspired)
- **Chat state:** Full-width messages (860px max) + bottom-docked composer with gradient fade
- **Top bar:** Compact sticky header — hamburger, logo, title, new-chat/clear buttons, locale switch, voice/narrate toggles, health indicator
- **Persistent sidebar:** CSS grid on desktop (>= 1024px), overlay on mobile
- **Removed:** Verbose hero section, two-column grid, info panels, section headers, card wrappers

### Session Management (`useChatStore.ts`)
- **Multi-conversation:** `conversations[]` array (max 50), `activeConversationId`, auto-save after each response
- **CRUD:** `createNewSession()`, `switchSession(id)`, `deleteSession(id)`
- **Auto-title:** Derived from first user message (60 chars)
- **Persisted:** localStorage via Zustand persist middleware

### Response Quality (`cleanResponse()`)
- **Chain-of-thought stripping:** Regex-based detection of 11 thinking signal patterns
- **Strategy:** Finds last contiguous cluster of answer paragraphs, discards all preceding reasoning
- **Deferred rendering:** Loading dots during streaming, clean answer on completion (no raw token display)

### Markdown Renderer (`Markdown.tsx`)
- **Auto-paragraph splitting:** `splitLongParagraph()` breaks text > 200 chars at sentence boundaries (~180 char chunks)
- **Disclaimer styling:** Content after `---` renders in smaller italic gray text
- **Typography:** `line-height: 1.75`, `letter-spacing: -0.01em`, `font-size: 0.9375rem`

### Composer (`ChatInput.tsx`)
- **Inline recording state:** Waveform animation + cancel (X) + confirm (checkmark) — no modal needed
- **Circular buttons:** 40px diameter, `border-radius: 50%` (Musawo-inspired)
- **Send icon:** Upward arrow (not paper plane)
- **Cancel recording:** `onCancelRecording` prop, calls `AudioRecorder.cancel()`

### Branding
- **URA logo:** Downloaded to `/public/ura-logo.png` (320x320 PNG, 6KB)
- **Top bar:** 28px logo next to title
- **Landing:** 140px hero logo (dominant visual anchor)
- **Chat watermark:** 280px ghost logo at 3% opacity behind messages
- **404 page:** Rebranded with URA navy/teal/gold

### Icons (`Icons.tsx`)
- **Added:** CloseIcon, PlusIcon, TrashIcon, MessageSquareIcon, MenuIcon, CheckIcon
- **Modified:** SendIcon (upward arrow), MicIcon (unchanged)

### API Routing
- **All calls via `/api/*` proxy** (CSP-safe, same-origin)
- **`.env.local`:** `INTERNAL_API_URL=http://127.0.0.1:8887`
- **Files fixed:** `page.tsx`, `useAnalyticsStore.ts`, `voiceService.ts`, `error.tsx`

### Mobile Responsive
- **Composer:** Fixed bottom dock on mobile (< 720px)
- **Sidebar:** Overlay with hamburger toggle
- **Top bar:** Collapses (hides title, labels → icons only)
- **Prompts:** Single column on mobile
- **Consent banner:** Above fixed composer

---

## Backend Changes

### System Prompt (`llm.py`)
- **Rule #1:** "OUTPUT THE ANSWER DIRECTLY. Do NOT include your reasoning, thinking, analysis of passages, or internal monologue."
- **`enable_thinking=False`:** Added to all 3 generation paths (sync, streaming, tool-use)

### Speech Service (`speech_service.py`)
- **`sys.path` fix:** Added project root so `ml.scripts.*` imports resolve
- **Sunbird AI integration:** New `sunbird.py` module — cloud fallback for STT, TTS, MT
- **STT chain:** Sunbird cloud → Local Sherpa → faster-whisper
- **TTS chain:** Sunbird cloud → Local Sherpa/Piper → edge-tts
- **MT chain:** Sunbird NLLB cloud → Local MT module
- **Deadline:** 20s → 60s (cloud API headroom)
- **PCM → WAV:** Conversion before Sunbird STT upload
- **MT backend:** `auto` → `prompted` (skip missing ONNX models)

### Packages Installed
- `faster-whisper` (CTranslate2 int8 STT)
- `edge-tts` (Microsoft neural TTS)

---

## AgriForge Changes (Companion Project)

- **Chat layout redesign:** Same Grok-inspired landing/chat split pattern
- **Inline recording UI:** Waveform + cancel/confirm in composer
- **Top bar:** Compact with menu, logo, status, clear/new-chat, locale
- **Starter prompts:** Reduced 6 → 4, send on click
- **Pre-existing fixes:** `StarterPrompts.tsx` framer-motion type, `useChat.ts` scope bug, viewport zoom
- **Accessibility:** `maximumScale: 5`

---

## Evaluation Results (2026-04-21)

| Metric | Value | Target | Status |
|--------|-------|--------|--------|
| Answer Rate | 100% | >= 80% | PASS |
| Avg Faithfulness | 0.930 | >= 0.70 | PASS |
| CoT Leak Rate | 0% | <= 5% | PASS |
| Red Team Block Rate | 80% | >= 80% | PASS |
| P50 Latency | 2.4s | <= 30s | PASS |
| P90 Latency | 46.2s | <= 60s | PASS |
| Speech Services | All available | Required | PASS |
| **Quality Gates** | **9/9 (100%)** | **100%** | **PASS** |

See: `docs/EVALUATION_REPORT.md` for full analysis with IEEE figures and LaTeX tables.
