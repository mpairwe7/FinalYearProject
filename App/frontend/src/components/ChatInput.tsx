import React, { memo, useLayoutEffect, useRef } from 'react';
import {
  MicIcon,
  SendIcon,
  CloseIcon,
  CheckIcon,
  PaperclipIcon,
  FileIcon,
  LoadingDots,
  VoiceWaveIcon,
} from './Icons';
import {
  ATTACHMENT_ACCEPT,
  MAX_ATTACHMENTS,
  PendingAttachment,
  formatDocType,
  formatFileSize,
} from '../lib/attachments';

interface ChatInputProps {
  message: string;
  isLoading: boolean;
  isRecording: boolean;
  isTransitioning: boolean;
  speechUnavailable: boolean;
  speechState: string;
  voiceMode: boolean;
  onMessageChange: (value: string) => void;
  onSend: () => void;
  onMicClick: () => void;
  onCancelRecording?: () => void;
  onFocus?: () => void;
  attachments?: PendingAttachment[];
  onAttachFiles?: (files: FileList) => void;
  onRemoveAttachment?: (clientId: string) => void;
  /* Voice mode is the composer's only conversation-level control. It renders
     only when its handler is provided. Language is NOT here — it is a
     session-level setting and lives in the header (see ChatHeader).

     There used to be two checkboxes here as well, "Voice" and "Narrate".
     They are gone: they spent 173px of a 370px phone row on two settings
     that describe one activity, and asking someone to tick "Voice" and then
     tick "Narrate" to hold a spoken conversation is a worse question than
     "do you want to talk to it?". Entering voice mode now turns narration on
     by itself, so the capability survives without the controls. */
  onVoiceModeChange?: (on: boolean) => void;
  voiceModeDisabled?: boolean;
}

/** Inline waveform — 5 animated bars */
function InlineWaveform() {
  return (
    <div className="composer-waveform" aria-hidden="true">
      <span /><span /><span /><span /><span />
    </div>
  );
}

function ChatInputInner({
  message,
  isLoading,
  isRecording,
  isTransitioning,
  speechUnavailable,
  speechState,
  voiceMode,
  onMessageChange,
  onSend,
  onMicClick,
  onCancelRecording,
  onFocus,
  attachments,
  onAttachFiles,
  onRemoveAttachment,
  onVoiceModeChange,
  voiceModeDisabled,
}: ChatInputProps) {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const isUploading = attachments?.some((a) => a.status === 'uploading') ?? false;
  // Drives the morph in the primary slot: nothing typed yet -> offer voice
  // mode; the moment there is something to send -> offer send. Trimmed, so a
  // stray space does not present a send button that refuses to send.
  const hasText = message.trim().length > 0;
  const canSend = hasText && !isLoading && !isUploading;

  useLayoutEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = 'auto';
    input.style.height = `${Math.min(input.scrollHeight, 144)}px`;
  }, [message]);

  // ── Recording state: in-composer waveform + cancel/confirm ──
  if (isRecording) {
    return (
      <>
        <div className="composer cmpv2 composer-active-recording">
          <div className="composer-rec-label">
            <span className="composer-rec-dot" aria-hidden="true" />
            Listening...
          </div>
          <div className="composer-rec-controls">
            <InlineWaveform />
            <button
              className="composer-rec-cancel"
              onClick={onCancelRecording}
              aria-label="Cancel recording"
            >
              <CloseIcon />
            </button>
            <button
              className="composer-rec-confirm"
              onClick={onMicClick}
              disabled={isTransitioning}
              aria-label="Send recording"
            >
              <CheckIcon />
            </button>
          </div>
        </div>
        <p className="composer-hint">Tap checkmark to send, or X to cancel.</p>
      </>
    );
  }

  // ── Normal state: two rows — textarea, then the toolbar ──
  const showAttachments = Boolean(onAttachFiles);
  return (
    <>
      {showAttachments && attachments && attachments.length > 0 && (
        <div className="composer-attachments" aria-label="Attached documents">
          {attachments.map((a) => (
            <div
              key={a.clientId}
              className={`attachment-chip ${a.status === 'error' ? 'attachment-chip-error' : ''}`}
            >
              <FileIcon />
              <span className="attachment-name" title={a.name}>{a.name}</span>
              <span className="attachment-meta">
                {a.status === 'uploading' && <LoadingDots />}
                {a.status === 'ready' && `${formatDocType(a.docType)} · ${formatFileSize(a.sizeBytes)}`}
                {a.status === 'error' && (a.error || 'Failed')}
              </span>
              <button
                type="button"
                className="attachment-remove"
                onClick={() => onRemoveAttachment?.(a.clientId)}
                aria-label={`Remove ${a.name}`}
              >
                <CloseIcon />
              </button>
            </div>
          ))}
        </div>
      )}
      <div className="composer cmpv2">
        <textarea
          ref={inputRef}
          className="input"
          id="composer-input"
          aria-label="Type your message"
          aria-multiline="true"
          placeholder={voiceMode ? 'Voice mode on — speak, or type' : 'Ask anything about URA...'}
          value={message}
          rows={1}
          enterKeyHint="send"
          spellCheck
          onChange={(e) => onMessageChange(e.target.value)}
          onFocus={onFocus}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              // sendMessage() no-ops while a reply streams; typing stays enabled.
              onSend();
            }
          }}
        />
        <div className="cmpv2-bar">
          {showAttachments && (
            <>
              <input
                ref={fileInputRef}
                type="file"
                multiple
                accept={ATTACHMENT_ACCEPT}
                className="attachment-file-input"
                aria-hidden="true"
                tabIndex={-1}
                onChange={(e) => {
                  if (e.target.files?.length) onAttachFiles?.(e.target.files);
                  e.target.value = '';
                }}
              />
              <button
                className="composer-circle-btn attach-circle-btn"
                onClick={() => fileInputRef.current?.click()}
                disabled={isLoading || (attachments?.length ?? 0) >= MAX_ATTACHMENTS}
                aria-label="Attach a document (PDF, Word, Excel, CSV, or image)"
                data-tip="Attach a document"
              >
                <PaperclipIcon />
              </button>
            </>
          )}
          <div className="cmpv2-spacer" />
          {/* Dictation — fills the textarea. Stays put in both states so it
              never moves under a thumb that is already reaching for it. */}
          <button
            className={`composer-circle-btn mic-circle-btn ${speechState === 'listening' ? 'btn-recording' : ''}`}
            onClick={onMicClick}
            disabled={speechUnavailable || isLoading || isTransitioning}
            aria-label={speechState === 'listening' ? 'Stop listening' : 'Start speaking'}
            /* The tip says "Dictate" while the accessible name stays "Start
               speaking": two speech controls sit side by side here, and the
               one thing a user must not have to guess is which one types and
               which one talks back. */
            data-tip={speechState === 'listening' ? 'Stop listening' : 'Dictate'}
          >
            <MicIcon />
          </button>
          {/* One primary slot, two jobs. Empty composer offers the thing you
              can actually do (talk); typing replaces it with send. Rendering
              both at once would leave a disabled send button sitting next to
              the mic for the whole of an empty composer. */}
          {canSend || !onVoiceModeChange ? (
            <button
              className="composer-circle-btn send-circle-btn"
              onClick={() => onSend()}
              disabled={isLoading || isUploading || !hasText}
              aria-label={isUploading ? 'Analysing attachment...' : 'Send message'}
              data-tip={isUploading ? 'Analysing…' : 'Send message'}
            >
              <SendIcon />
            </button>
          ) : (
            <button
              className={`composer-circle-btn voicemode-circle-btn ${voiceMode ? 'is-active' : ''}`}
              onClick={() => onVoiceModeChange(!voiceMode)}
              disabled={voiceModeDisabled}
              aria-pressed={voiceMode}
              aria-label={voiceMode ? 'Exit voice mode' : 'Enter voice mode'}
              data-tip={voiceMode ? 'Exit voice mode' : 'Enter voice mode'}
            >
              <VoiceWaveIcon />
            </button>
          )}
        </div>
      </div>
      {/* The hint carries what the removed toggles used to say — that voice
          mode answers aloud — so nothing is only discoverable by tooltip. */}
      <p className="composer-hint">
        {voiceMode
          ? 'Voice mode: tap the mic to speak, and replies are read back to you.'
          : 'URA Assistant can make mistakes. Verify important tax information at ura.go.ug.'}
      </p>
    </>
  );
}

const ChatInput = memo(ChatInputInner);

export default ChatInput;
