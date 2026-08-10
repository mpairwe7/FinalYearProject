import React, { memo, useLayoutEffect, useRef } from 'react';
import { MicIcon, SendIcon, CloseIcon, CheckIcon, PaperclipIcon, FileIcon, LoadingDots } from './Icons';
import LanguageMenu, { LanguageOption } from './LanguageMenu';
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
  /* chatv2 — controls relocated from the header into the composer toolbar.
     All optional: the control renders only when its handler is provided. */
  locale?: string;
  localeOptions?: readonly LanguageOption[];
  onLocaleChange?: (code: string) => void;
  onVoiceModeChange?: (on: boolean) => void;
  voiceModeDisabled?: boolean;
  autoNarrate?: boolean;
  onAutoNarrateChange?: (on: boolean) => void;
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
  locale,
  localeOptions,
  onLocaleChange,
  onVoiceModeChange,
  voiceModeDisabled,
  autoNarrate,
  onAutoNarrateChange,
}: ChatInputProps) {
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const isUploading = attachments?.some((a) => a.status === 'uploading') ?? false;

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
          placeholder={voiceMode ? 'Voice mode on — tap mic or type...' : 'Ask anything about URA...'}
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
          {onLocaleChange && localeOptions && locale !== undefined && (
            <LanguageMenu
              locale={locale}
              options={localeOptions}
              onLocaleChange={onLocaleChange}
            />
          )}
          {onVoiceModeChange && (
            <label className="voice-toggle" title="Voice mode: server-side ASR + TTS">
              <input
                type="checkbox"
                checked={voiceMode}
                onChange={(e) => onVoiceModeChange(e.target.checked)}
                disabled={voiceModeDisabled}
              />
              <span className="voice-toggle-label">Voice</span>
            </label>
          )}
          {onAutoNarrateChange && (
            <label className="voice-toggle" title="Auto-read replies aloud">
              <input
                type="checkbox"
                checked={autoNarrate ?? false}
                onChange={(e) => onAutoNarrateChange(e.target.checked)}
              />
              <span className="voice-toggle-label">Narrate</span>
            </label>
          )}
          <div className="cmpv2-spacer" />
          <button
            className={`composer-circle-btn mic-circle-btn ${speechState === 'listening' ? 'btn-recording' : ''}`}
            onClick={onMicClick}
            disabled={speechUnavailable || isLoading || isTransitioning}
            aria-label={speechState === 'listening' ? 'Stop listening' : 'Start speaking'}
            data-tip={speechState === 'listening' ? 'Stop listening' : 'Start speaking'}
          >
            <MicIcon />
          </button>
          <button
            className="composer-circle-btn send-circle-btn"
            onClick={() => onSend()}
            disabled={isLoading || isUploading || !message.trim()}
            aria-label={isUploading ? 'Analysing attachment...' : 'Send message'}
            data-tip={isUploading ? 'Analysing…' : 'Send message'}
          >
            <SendIcon />
          </button>
        </div>
      </div>
      <p className="composer-hint">
        {voiceMode
          ? 'Tap mic to record, then your voice is transcribed, answered, and narrated back.'
          : 'URA Assistant can make mistakes. Verify important tax information at ura.go.ug.'}
      </p>
    </>
  );
}

const ChatInput = memo(ChatInputInner);

export default ChatInput;
