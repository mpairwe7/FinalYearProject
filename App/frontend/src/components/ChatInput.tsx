import React, { memo, useLayoutEffect, useRef } from 'react';
import { useTranslation } from '../lib/i18n';
import {
  MicIcon,
  SendIcon,
  CloseIcon,
  CheckIcon,
  PaperclipIcon,
  FileIcon,
  LoadingDots,
  VoiceWaveIcon,
  StopIcon,
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
  /* A one-line result from the last dictation attempt, shown in the hint slot.
     Without it, dictation that heard nothing was a dead end: the recording
     panel closed, the composer stayed empty, and nothing said why. */
  dictationNotice?: string | null;
  /** Abort an in-flight reply. When set, the primary slot becomes Stop while loading. */
  onStop?: () => void;
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
  dictationNotice,
  onStop,
}: ChatInputProps) {
  const t = useTranslation();
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
  //
  // Two flows land here and the checkmark means different things in each. In
  // voice mode it sends the utterance as a turn. In dictation it does not send
  // anything — it stops the recording and drops the transcript into the
  // composer for you to edit. The panel said "Send recording" and "Tap
  // checkmark to send" either way, which promised the wrong outcome to anyone
  // dictating: they tap expecting their question to go, and get text in a box.
  // Both the accessible name and the hint follow the flow now.
  if (isRecording) {
    const confirmLabel = voiceMode ? t('composer.sendRecording') : t('composer.stopAndInsert');
    return (
      <>
        <div className="composer cmpv2 composer-active-recording">
          <div className="composer-rec-label">
            <span className="composer-rec-dot" aria-hidden="true" />
            {t('composer.listening')}
          </div>
          <div className="composer-rec-controls">
            <InlineWaveform />
            <button
              className="composer-rec-cancel"
              data-testid="composer-rec-cancel"
              onClick={onCancelRecording}
              aria-label={t('composer.cancelRecording')}
            >
              <CloseIcon />
            </button>
            <button
              className="composer-rec-confirm"
              data-testid="composer-rec-confirm"
              onClick={onMicClick}
              disabled={isTransitioning}
              aria-label={confirmLabel}
              data-tip={confirmLabel}
            >
              <CheckIcon />
            </button>
          </div>
        </div>
        <p className="composer-hint">
          {voiceMode
            ? t('composer.recHintVoice')
            : t('composer.recHintDictation')}
        </p>
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
          aria-label={t('composer.label')}
          aria-multiline="true"
          placeholder={voiceMode ? t('composer.placeholderVoice') : t('composer.placeholder')}
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
          {/* Dictation — fills the textarea. Stays put in all three states so
              it never moves under a thumb that is already reaching for it.
              `processing` only appears on the server-ASR path, where the
              transcript arrives over the network: without it the button just
              sat there looking idle while the upload was in flight, and the
              second tap that invites cancels nothing and loses the recording. */}
          <button
            className={`composer-circle-btn mic-circle-btn ${speechState === 'listening' ? 'btn-recording' : ''} ${speechState === 'processing' ? 'is-processing' : ''}`}
            onClick={onMicClick}
            disabled={speechUnavailable || isLoading || isTransitioning || speechState === 'processing'}
            /* The tip says "Dictate" while the accessible name stays "Start
               speaking": two speech controls sit side by side here, and the
               one thing a user must not have to guess is which one types and
               which one talks back. */
            aria-label={
              speechState === 'listening'
                ? t('composer.micStop')
                : speechState === 'processing'
                  ? t('composer.transcribing')
                  : t('composer.micStart')
            }
            data-tip={
              speechState === 'listening'
                ? t('composer.micStop')
                : speechState === 'processing'
                  ? t('composer.transcribingTip')
                  : t('composer.dictate')
            }
            data-testid="composer-mic"
          >
            <MicIcon />
          </button>
          {/* One primary slot, two jobs. Empty composer offers the thing you
              can actually do (talk); typing replaces it with send. Rendering
              both at once would leave a disabled send button sitting next to
              the mic for the whole of an empty composer. */}
          {isLoading && onStop ? (
            <button
              className="composer-circle-btn send-circle-btn stop-circle-btn"
              onClick={onStop}
              aria-label="Stop generating"
              data-tip={t('composer.stop')}
            >
              <StopIcon />
            </button>
          ) : canSend || !onVoiceModeChange ? (
            <button
              className="composer-circle-btn send-circle-btn"
              data-testid="composer-send"
              onClick={() => onSend()}
              disabled={isLoading || isUploading || !hasText}
              aria-label={isUploading ? t('composer.analysing') : t('composer.send')}
              data-tip={isUploading ? t('composer.analysingTip') : t('composer.send')}
            >
              <SendIcon />
            </button>
          ) : (
            <button
              className={`composer-circle-btn voicemode-circle-btn ${voiceMode ? 'is-active' : ''}`}
              onClick={() => onVoiceModeChange(!voiceMode)}
              disabled={voiceModeDisabled}
              aria-pressed={voiceMode}
              data-testid="composer-voicemode"
              aria-label={voiceMode ? t('composer.voiceExit') : t('composer.voiceEnter')}
              data-tip={voiceMode ? t('composer.voiceExit') : t('composer.voiceEnter')}
            >
              <VoiceWaveIcon />
            </button>
          )}
        </div>
      </div>
      {/* The hint carries what the removed toggles used to say — that voice
          mode answers aloud — so nothing is only discoverable by tooltip. A
          dictation result takes the slot while it is there: it is about the
          thing that just happened, so it outranks a standing disclaimer.
          role=status announces it without stealing focus from the composer. */}
      <p className={`composer-hint${dictationNotice ? ' composer-hint-notice' : ''}`} role="status">
        {dictationNotice
          ? dictationNotice
          : voiceMode
            ? t('composer.voiceHint')
            : t('composer.disclaimer')}
      </p>
    </>
  );
}

const ChatInput = memo(ChatInputInner);

export default ChatInput;
