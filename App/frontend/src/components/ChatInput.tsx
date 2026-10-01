import React, { memo, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useTranslation } from '../lib/i18n';
import { useConnectorStore } from '../store/useConnectorStore';
import { CameraCapture } from './CameraCapture';
import {
  MicIcon,
  SendIcon,
  CloseIcon,
  CheckIcon,
  FileIcon,
  LoadingDots,
  VoiceWaveIcon,
  StopIcon,
  DownloadIcon,
  EyeIcon,
  PlugIcon,
  CameraIcon,
  PlusIcon,
  EfrisLogo,
  DtsLogo,
  UrsbLogo,
  BwimsLogo,
  TinLogo,
  PaymentLogo,
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
  onInspectAttachment?: (attachment: PendingAttachment) => void;
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
  /** Live audio frequency levels [0..1] for responsive waveform */
  audioLevels?: number[];
}

/** Inline waveform — 5 bars responsive to live microphone levels when available */
function InlineWaveform({ levels }: { levels?: number[] }) {
  const hasLevels = levels && levels.length >= 5 && levels.some((v) => v > 0.05);
  return (
    <div className="composer-waveform" aria-hidden="true">
      {Array.from({ length: 5 }).map((_, i) => {
        const val = hasLevels ? Math.min(1.0, Math.max(0.15, levels[i] ?? 0.2)) : null;
        return (
          <span
            key={i}
            style={
              val !== null
                ? {
                    transform: `scaleY(${val})`,
                    animation: 'none',
                    transition: 'transform 0.08s ease-out',
                  }
                : undefined
            }
          />
        );
      })}
    </div>
  );
}

function getMiniLogo(id: string) {
  switch (id) {
    case 'efris':
      return <EfrisLogo size={14} />;
    case 'digital_tax_stamps':
      return <DtsLogo size={14} />;
    case 'ursb':
      return <UrsbLogo size={14} />;
    case 'bwims':
      return <BwimsLogo size={14} />;
    case 'tin_registration':
      return <TinLogo size={14} />;
    case 'payment_system':
      return <PaymentLogo size={14} />;
    default:
      return null;
  }
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
  onInspectAttachment,
  onVoiceModeChange,
  voiceModeDisabled,
  dictationNotice,
  onStop,
  audioLevels,
}: ChatInputProps) {
  const t = useTranslation();
  const { connectors, activeConnectorIds, openModal, toggleConnector } = useConnectorStore();
  const [isDragging, setIsDragging] = useState(false);
  const [showAttachMenu, setShowAttachMenu] = useState(false);
  const [isCameraActive, setIsCameraActive] = useState(false);
  const attachMenuRef = useRef<HTMLDivElement>(null);
  const dragCounterRef = useRef(0);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const cameraInputRef = useRef<HTMLInputElement>(null);
  const isUploading = attachments?.some((a) => a.status === 'uploading') ?? false;

  const handleCameraCapture = (imageBase64: string) => {
    setIsCameraActive(false);
    if (!onAttachFiles) return;

    try {
      const arr = imageBase64.split(',');
      const mime = arr[0].match(/:(.*?);/)?.[1] || 'image/jpeg';
      const bstr = atob(arr[1]);
      let n = bstr.length;
      const u8arr = new Uint8Array(n);
      while (n--) {
        u8arr[n] = bstr.charCodeAt(n);
      }
      const file = new File(
        [u8arr],
        `ura_photo_capture_${Date.now().toString().slice(-4)}.jpg`,
        { type: mime }
      );
      if (typeof DataTransfer !== 'undefined') {
        const dt = new DataTransfer();
        dt.items.add(file);
        onAttachFiles(dt.files);
      }
    } catch (err) {
      console.error('Failed to process camera capture', err);
    }
  };

  const handleTakePhotoClick = () => {
    setShowAttachMenu(false);
    if (typeof navigator !== 'undefined' && navigator.mediaDevices && window.innerWidth >= 768) {
      setIsCameraActive(true);
    } else {
      cameraInputRef.current?.click();
    }
  };

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (attachMenuRef.current && !attachMenuRef.current.contains(event.target as Node)) {
        setShowAttachMenu(false);
      }
    }
    if (showAttachMenu) {
      document.addEventListener('mousedown', handleClickOutside);
      return () => document.removeEventListener('mousedown', handleClickOutside);
    }
  }, [showAttachMenu]);
  // Drives the morph in the primary slot: nothing typed yet -> offer voice
  // mode; the moment there is something to send -> offer send. Trimmed, so a
  // stray space does not present a send button that refuses to send.
  const hasText = message.trim().length > 0;
  const canSend = hasText && !isLoading && !isUploading;

  const handleDragEnter = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current += 1;
    if (e.dataTransfer.items && e.dataTransfer.items.length > 0) {
      setIsDragging(true);
    }
  };

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    dragCounterRef.current -= 1;
    if (dragCounterRef.current <= 0) {
      setIsDragging(false);
      dragCounterRef.current = 0;
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
    dragCounterRef.current = 0;
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      onAttachFiles?.(e.dataTransfer.files);
    }
  };

  const handlePaste = (e: React.ClipboardEvent<HTMLTextAreaElement>) => {
    if (e.clipboardData && e.clipboardData.files && e.clipboardData.files.length > 0) {
      const files = e.clipboardData.files;
      const fileList: File[] = [];
      for (let i = 0; i < files.length; i++) {
        const file = files[i];
        if (file.type.startsWith('image/')) {
          const name =
            file.name && file.name !== 'image.png'
              ? file.name
              : `ura_portal_screenshot_${Date.now().toString().slice(-4)}.png`;
          fileList.push(new File([file], name, { type: file.type }));
        } else {
          fileList.push(file);
        }
      }
      if (fileList.length > 0 && typeof DataTransfer !== 'undefined') {
        e.preventDefault();
        const dt = new DataTransfer();
        fileList.forEach((f) => dt.items.add(f));
        onAttachFiles?.(dt.files);
        if (!message.trim()) {
          onMessageChange('Please inspect this URA portal screenshot and guide me on how to resolve the issue.');
        }
      }
    }
  };

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
            <InlineWaveform levels={audioLevels} />
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
      <div
        className={`composer cmpv2 ${isDragging ? 'composer-drag-active' : ''}`}
        onDragEnter={handleDragEnter}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
        onDrop={handleDrop}
      >
        {isDragging && (
          <div className="composer-drop-overlay" aria-hidden="true">
            <span>Drop documents here to analyze and attach (PDF, Word, Excel, CSV, or Image)</span>
          </div>
        )}
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
                {a.status === 'ready' && (
                  <button
                    type="button"
                    className="attachment-report-link"
                    onClick={() => onInspectAttachment?.(a)}
                    title="Inspect extracted fields & tax audit"
                    aria-label={`Inspect ${a.name}`}
                  >
                    <EyeIcon />
                  </button>
                )}
                {a.status === 'ready' && a.documentId && (
                  <a
                    href={`/api/v1/documents/${a.documentId}/report`}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="attachment-report-link"
                    title="Download analysis report"
                    aria-label={`Download analysis report for ${a.name}`}
                  >
                    <DownloadIcon />
                  </a>
                )}
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
        {/* Active System Connectors Bar (inspired by Grok apps) */}
        {activeConnectorIds.length > 0 && (
          <div className="composer-connector-bar flex flex-wrap items-center gap-1.5 px-3 pt-2 pb-1" aria-label="Active system connectors">
            {activeConnectorIds.map((id) => {
              const c = connectors.find((item) => item.id === id);
              if (!c) return null;
              const shortName =
                c.id === 'digital_tax_stamps'
                  ? 'DTS'
                  : c.id === 'tin_registration'
                  ? 'TIN'
                  : c.id === 'payment_system'
                  ? 'PAYMENTS'
                  : c.id.toUpperCase();
              return (
                <span
                  key={c.id}
                  className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 hover:bg-emerald-500/15 transition cursor-pointer select-none"
                  onClick={() => openModal(c.id)}
                  title={`${c.name} (Independent DB: ${c.database?.database || c.id + '_system.db'})`}
                >
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
                  {getMiniLogo(c.id)}
                  <span className="font-semibold">{shortName}</span>
                  <span className="text-emerald-400/80">· Active</span>
                  <button
                    type="button"
                    className="text-emerald-400/50 hover:text-emerald-200 text-xs px-0.5 leading-none ml-0.5"
                    onClick={(e) => {
                      e.stopPropagation();
                      toggleConnector(c.id);
                    }}
                    title={`Disconnect ${c.name}`}
                    aria-label={`Disconnect ${c.name}`}
                  >
                    ×
                  </button>
                </span>
              );
            })}
            <button
              type="button"
              onClick={() => openModal()}
              className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded-full text-[11px] font-medium bg-neutral-800/90 border border-neutral-700/80 text-neutral-300 hover:text-white hover:border-neutral-600 transition"
              title="Add or configure URA system connectors"
            >
              <PlugIcon size={11} />
              <span>+ Add Connector</span>
            </button>
          </div>
        )}

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
          onPaste={handlePaste}
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
              <input
                ref={cameraInputRef}
                type="file"
                accept="image/*"
                capture="environment"
                className="attachment-file-input"
                aria-hidden="true"
                tabIndex={-1}
                onChange={(e) => {
                  if (e.target.files?.length) onAttachFiles?.(e.target.files);
                  e.target.value = '';
                }}
              />
              <div className="relative" ref={attachMenuRef}>
                <button
                  type="button"
                  className={`composer-circle-btn add-circle-btn ${showAttachMenu ? 'bg-neutral-700 text-white' : ''}`}
                  onClick={() => setShowAttachMenu((prev) => !prev)}
                  disabled={isLoading || (attachments?.length ?? 0) >= MAX_ATTACHMENTS}
                  aria-label="Attach a document (PDF, Word, Excel, CSV, or image), take a photo, or add connector"
                  data-tip="Add (File, Photo, Connector)"
                  data-testid="composer-add-btn"
                >
                  <PlusIcon size={18} />
                </button>

                {showAttachMenu && (
                  <div className="absolute bottom-full left-0 mb-2 w-72 p-1.5 rounded-xl bg-neutral-900 border border-neutral-700 shadow-2xl z-50 text-neutral-200 animate-fade-in">
                    {/* Option 1: Upload a file */}
                    <button
                      type="button"
                      className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-xs hover:bg-neutral-800 transition text-left group"
                      onClick={() => {
                        setShowAttachMenu(false);
                        fileInputRef.current?.click();
                      }}
                    >
                      <div className="p-1.5 rounded-md bg-neutral-800 border border-neutral-700 text-neutral-300 group-hover:text-white">
                        <FileIcon />
                      </div>
                      <div>
                        <div className="font-semibold text-white">Upload a file</div>
                        <div className="text-[10px] text-neutral-400">PDF, Word, Excel, CSV, or image</div>
                      </div>
                    </button>

                    {/* Option 2: Take a photo */}
                    <button
                      type="button"
                      className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-xs hover:bg-neutral-800 transition text-left group border-t border-neutral-800/80"
                      onClick={handleTakePhotoClick}
                    >
                      <div className="p-1.5 rounded-md bg-neutral-800 border border-neutral-700 text-neutral-300 group-hover:text-white">
                        <CameraIcon />
                      </div>
                      <div>
                        <div className="font-semibold text-white">Take a photo</div>
                        <div className="text-[10px] text-neutral-400">Snap National ID, receipt, or physical doc</div>
                      </div>
                    </button>

                    {/* Option 3: Add connector */}
                    <button
                      type="button"
                      className="w-full flex items-center gap-3 px-3 py-2 rounded-lg text-xs hover:bg-neutral-800 transition text-left group border-t border-neutral-800/80"
                      onClick={() => {
                        setShowAttachMenu(false);
                        openModal();
                      }}
                    >
                      <div className="p-1.5 rounded-md bg-emerald-500/20 text-emerald-400 border border-emerald-500/30">
                        <PlugIcon size={14} />
                      </div>
                      <div className="flex-1">
                        <div className="font-semibold text-white flex items-center justify-between">
                          <span>Add connector</span>
                          <span className="text-[10px] text-emerald-400 font-mono">
                            {activeConnectorIds.length}/{connectors.length} Connected
                          </span>
                        </div>
                        <div className="text-[10px] text-neutral-400">
                          EFRIS, DTS, URSB, BWIMS, TIN &amp; Payments
                        </div>
                      </div>
                    </button>
                  </div>
                )}
              </div>
            </>
          )}
          <div className="cmpv2-spacer" />
          {/* Dictation — fills the textarea. Stays put in every state so it
              never moves under a thumb that is already reaching for it.

              `starting` is the state a reported bug asked for: opening a mic
              is slow (a permission prompt the first time, then engine
              warm-up), and while the button said "Start speaking" and looked
              untouched people pressed it again — which threw inside the
              speech engine and left the mic in an error state, so a third
              press was needed. It says what it is doing now, and refuses the
              press that used to break it.

              `processing` only appears on the server-ASR path, where the
              transcript arrives over the network: without it the button just
              sat there looking idle while the upload was in flight, and the
              second tap that invites cancels nothing and loses the recording. */}
          <button
            className={`composer-circle-btn mic-circle-btn ${speechState === 'listening' ? 'btn-recording' : ''} ${speechState === 'processing' || speechState === 'starting' ? 'is-processing' : ''}`}
            onClick={onMicClick}
            disabled={
              speechUnavailable ||
              isLoading ||
              isTransitioning ||
              speechState === 'processing' ||
              speechState === 'starting'
            }
            /* The tip says "Dictate" while the accessible name stays "Start
               speaking": two speech controls sit side by side here, and the
               one thing a user must not have to guess is which one types and
               which one talks back. */
            aria-label={
              speechState === 'listening'
                ? t('composer.micStop')
                : speechState === 'starting'
                  ? t('composer.micStarting')
                  : speechState === 'processing'
                    ? t('composer.transcribing')
                    : t('composer.micStart')
            }
            data-tip={
              speechState === 'listening'
                ? t('composer.micStop')
                : speechState === 'starting'
                  ? t('composer.micStartingTip')
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

      {isCameraActive && (
        <CameraCapture
          active={isCameraActive}
          onCapture={handleCameraCapture}
          onClose={() => setIsCameraActive(false)}
        />
      )}
    </>
  );
}

const ChatInput = memo(ChatInputInner);

export default ChatInput;
