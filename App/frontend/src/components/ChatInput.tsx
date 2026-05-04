import React, { memo, useLayoutEffect, useRef } from 'react';
import { MicIcon, SendIcon, CloseIcon, CheckIcon } from './Icons';

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
}: ChatInputProps) {
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    const input = inputRef.current;
    if (!input) return;
    input.style.height = 'auto';
    input.style.height = `${Math.min(input.scrollHeight, 144)}px`;
  }, [message]);

  // ── Recording state: Grok-inspired inline waveform + cancel/confirm ──
  if (isRecording) {
    return (
      <>
        <div className="composer composer-active-recording">
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

  // ── Normal state ──
  return (
    <>
      <div className="composer">
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
              onSend();
            }
          }}
          disabled={isLoading}
        />
        <button
          className={`composer-circle-btn mic-circle-btn ${speechState === 'listening' ? 'btn-recording' : ''}`}
          onClick={onMicClick}
          disabled={speechUnavailable || isLoading || isTransitioning}
          aria-label={speechState === 'listening' ? 'Stop listening' : 'Start speaking'}
        >
          <MicIcon />
        </button>
        <button
          className="composer-circle-btn send-circle-btn"
          onClick={() => onSend()}
          disabled={isLoading || !message.trim()}
          aria-label="Send message"
        >
          <SendIcon />
        </button>
      </div>
      <p className="composer-hint">
        {voiceMode
          ? 'Tap mic to record, then your voice is transcribed, answered, and narrated back.'
          : 'Press Enter to send, or tap the mic to dictate.'}
      </p>
    </>
  );
}

const ChatInput = memo(ChatInputInner);

export default ChatInput;
