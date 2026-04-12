import React, { memo } from 'react';
import { MicIcon, SendIcon } from './Icons';

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
}: ChatInputProps) {
  return (
    <>
      <div className="composer">
        <input
          className="input"
          id="composer-input"
          aria-label="Type your message"
          placeholder={voiceMode ? 'Voice mode on — tap mic or type...' : 'Ask anything about URA...'}
          value={message}
          onChange={(e) => onMessageChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              onSend();
            }
          }}
          disabled={isLoading}
        />
        <button
          className={`button secondary ${isRecording ? 'btn-recording' : ''}`}
          onClick={onMicClick}
          disabled={speechUnavailable || isLoading || isTransitioning}
          aria-label={isRecording ? 'Stop recording' : speechState === 'listening' ? 'Stop listening' : 'Start speaking'}
        >
          <MicIcon />
          {isRecording ? 'Stop' : speechState === 'listening' ? 'Stop' : 'Speak'}
        </button>
        <button
          className="button"
          onClick={onSend}
          disabled={isLoading || !message.trim()}
          aria-label="Send message"
        >
          <SendIcon /> Send
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
