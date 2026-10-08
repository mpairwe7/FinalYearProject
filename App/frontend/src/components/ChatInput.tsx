import React, { memo, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { useTranslation } from '../lib/i18n';
import { CameraCapture } from './CameraCapture';
import { ModalDialog } from './ModalDialog';
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
  CameraIcon,
  PlugIcon,
  PlusIcon,
} from './Icons';
import {
  ATTACHMENT_ACCEPT,
  MAX_ATTACHMENTS,
  PendingAttachment,
  formatDocType,
  formatFileSize,
} from '../lib/attachments';
import type { ChatConnector } from '../lib/connectors';

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
  /** Attach files and return the subset accepted for upload. */
  onAttachFiles?: (files: FileList) => File[];
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
  /** Voice mode sends the turn by itself when the speaker pauses (Settings → Voice). */
  autoSend?: boolean;
  chatConnectors?: ChatConnector[];
  connectorsEnabled?: boolean;
  connectorsLoading?: boolean;
  connectorsError?: boolean;
  selectedConnectorNamespaces?: string[];
  onOpenConnectors?: () => void;
  onToggleConnector?: (namespace: string) => void;
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
  autoSend = false,
  chatConnectors = [],
  connectorsEnabled = false,
  connectorsLoading = false,
  connectorsError = false,
  selectedConnectorNamespaces = [],
  onOpenConnectors,
  onToggleConnector,
}: ChatInputProps) {
  const t = useTranslation();
  const [isDragging, setIsDragging] = useState(false);
  const [showAttachMenu, setShowAttachMenu] = useState(false);
  const [showConnectorsDialog, setShowConnectorsDialog] = useState(false);
  const [isCameraActive, setIsCameraActive] = useState(false);
  const attachMenuRef = useRef<HTMLDivElement>(null);
  const dragCounterRef = useRef(0);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const cameraInputRef = useRef<HTMLInputElement>(null);
  const isUploading = attachments?.some((a) => a.status === 'uploading') ?? false;
  const attachmentLimitReached = (attachments?.length ?? 0) >= MAX_ATTACHMENTS;
  const selectedConnectors = chatConnectors.filter((connector) =>
    selectedConnectorNamespaces.includes(connector.namespace),
  );

  const handleAttachFiles = useCallback((files: FileList) => {
    if (!onAttachFiles || files.length === 0) return;
    const acceptedFiles = onAttachFiles(files);
    const includesAcceptedImage = acceptedFiles.some((file) => file.type.startsWith('image/'));
    if (includesAcceptedImage && !message.trim()) {
      onMessageChange(t('composer.imagePrompt'));
    }
  }, [message, onAttachFiles, onMessageChange, t]);

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
        handleAttachFiles(dt.files);
      }
    } catch {
      console.error('Failed to process camera capture');
    }
  };

  const [addMenuFocusIdx, setAddMenuFocusIdx] = useState<number | null>(null);
  const addBtnRef = useRef<HTMLButtonElement>(null);
  const addMenuPanelRef = useRef<HTMLDivElement>(null);
  const addMenuOptionRefs = useRef<(HTMLButtonElement | null)[]>([]);

  const closeConnectorsDialog = () => {
    setShowConnectorsDialog(false);
    window.requestAnimationFrame(() => addBtnRef.current?.focus());
  };

  const closeAddMenu = useCallback(() => {
    setAddMenuFocusIdx(null);
    setShowAttachMenu(false);
    addBtnRef.current?.focus();
  }, [setAddMenuFocusIdx, setShowAttachMenu]);

  const handleTakePhotoClick = () => {
    closeAddMenu();
    if (typeof navigator !== 'undefined' && navigator.mediaDevices && window.innerWidth >= 768) {
      setIsCameraActive(true);
    } else {
      cameraInputRef.current?.click();
    }
  };

  const addMenuOptionIndices = [0, 1, 2].filter(
    (idx) => !attachmentLimitReached || idx === 2,
  );
  const addMenuRovingIdx =
    addMenuFocusIdx !== null && addMenuOptionIndices.includes(addMenuFocusIdx)
      ? addMenuFocusIdx
      : addMenuOptionIndices[0];

  // Focus active option on open; lock body scroll; trap Tab and Escape
  useEffect(() => {
    if (!showAttachMenu) return;
    addMenuOptionRefs.current[addMenuRovingIdx]?.focus();
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.preventDefault();
        closeAddMenu();
        return;
      }
      if (e.key !== 'Tab') return;
      const focusables = addMenuPanelRef.current?.querySelectorAll<HTMLElement>(
        'button:not([disabled])'
      );
      if (!focusables || focusables.length === 0) return;
      const first = focusables[0];
      const last = focusables[focusables.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    };
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
    };
  }, [showAttachMenu, closeAddMenu, addMenuRovingIdx]);

  const onAddMenuOptionKey = (e: React.KeyboardEvent, idx: number) => {
    const move = (position: number) => {
      const clampedPosition = (position + addMenuOptionIndices.length) % addMenuOptionIndices.length;
      const nextIdx = addMenuOptionIndices[clampedPosition];
      setAddMenuFocusIdx(nextIdx);
      addMenuOptionRefs.current[nextIdx]?.focus();
    };
    const position = addMenuOptionIndices.indexOf(idx);
    if (e.key === 'ArrowDown' || e.key === 'ArrowRight') {
      e.preventDefault();
      move(position + 1);
    } else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') {
      e.preventDefault();
      move(position - 1);
    } else if (e.key === 'Home') {
      e.preventDefault();
      move(0);
    } else if (e.key === 'End') {
      e.preventDefault();
      move(addMenuOptionIndices.length - 1);
    }
  };
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
      handleAttachFiles(e.dataTransfer.files);
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
        handleAttachFiles(dt.files);
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
        <p className="composer-hint composer-hint-rec">
          {voiceMode
            ? t(autoSend ? 'composer.recHintVoiceAuto' : 'composer.recHintVoice')
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
            <span>{t('composer.dropFiles')}</span>
          </div>
        )}
        {showAttachments && attachments && attachments.length > 0 && (
          <>
            <div className="composer-attachments" aria-label={t('composer.attachmentsLabel')}>
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
                      title={t('documents.inspect')}
                      aria-label={t('composer.inspectAttachment', { name: a.name })}
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
                      title={t('documents.downloadReport')}
                      aria-label={t('composer.downloadAttachmentReport', { name: a.name })}
                    >
                      <DownloadIcon />
                    </a>
                  )}
                  <button
                    type="button"
                    className="attachment-remove"
                    onClick={() => onRemoveAttachment?.(a.clientId)}
                    aria-label={t('composer.removeAttachmentNamed', { name: a.name })}
                  >
                    <CloseIcon />
                  </button>
                </div>
              ))}
            </div>
            <p className="composer-attachment-privacy" role="note">
              {t('composer.attachPrivacyNotice')}
            </p>
          </>
        )}

        {selectedConnectors.length > 0 && (
          <div className="composer-connectors" role="group" aria-label={t('composer.selectedConnectors')}>
            {selectedConnectors.map((connector) => (
              <span key={connector.namespace} className="composer-connector-chip">
                <PlugIcon />
                <span>{connector.label}</span>
                <button
                  type="button"
                  className="composer-connector-remove"
                  onClick={() => onToggleConnector?.(connector.namespace)}
                  aria-label={t('composer.removeConnector', { name: connector.label })}
                  disabled={isLoading}
                >
                  <CloseIcon />
                </button>
              </span>
            ))}
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
                  if (e.target.files?.length) handleAttachFiles(e.target.files);
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
                  if (e.target.files?.length) handleAttachFiles(e.target.files);
                  e.target.value = '';
                }}
              />
              <div className="relative" ref={attachMenuRef}>
                <button
                  ref={addBtnRef}
                  type="button"
                  className={`composer-circle-btn add-circle-btn transition-all duration-200 ${
                    showAttachMenu
                      ? 'is-active bg-neutral-800 text-white border border-neutral-700 shadow-sm'
                      : 'text-neutral-400 hover:text-white hover:bg-neutral-800/80'
                  }`}
                  onClick={() => {
                    setAddMenuFocusIdx(0);
                    setShowAttachMenu((prev) => !prev);
                  }}
                  disabled={isLoading}
                  aria-label={t('composer.addToConversation')}
                  aria-haspopup="dialog"
                  aria-expanded={showAttachMenu}
                  title={t('composer.addTitle')}
                  data-tip={t('composer.addTitle')}
                  data-testid="composer-add-btn"
                >
                  <PlusIcon
                    size={18}
                    className={`transition-transform duration-200 ease-out ${
                      showAttachMenu ? 'rotate-45 text-white' : ''
                    }`}
                  />
                </button>

                {showAttachMenu && (
                  <div
                    className="lmv2-overlay addmenu-overlay"
                    onMouseDown={(e) => {
                      if (e.target === e.currentTarget) closeAddMenu();
                    }}
                  >
                    <div
                      ref={addMenuPanelRef}
                      className="lmv2 addmenu-dialog"
                      role="dialog"
                      aria-modal="true"
                      aria-labelledby="composer-attach-title"
                    >
                      <>
                          <div className="lmv2-head addmenu-head">
                            <div className="flex items-center gap-2">
                              <div className="w-6 h-6 rounded-md bg-neutral-800 border border-neutral-700 flex items-center justify-center text-neutral-300">
                                <PlusIcon size={14} />
                              </div>
                              <h2 id="composer-attach-title">{t('composer.addTitle')}</h2>
                            </div>
                            <button
                              type="button"
                              className="dlgv2-x lmv2-x"
                              onClick={closeAddMenu}
                              aria-label={t('common.close')}
                            >
                              <CloseIcon />
                            </button>
                          </div>

                          <div className="lmv2-list addmenu-list" role="menu" aria-label={t('composer.attachmentOptions')}>
                            {/* Option 1: Upload a file */}
                            <button
                              ref={(el) => {
                                addMenuOptionRefs.current[0] = el;
                              }}
                              type="button"
                              role="menuitem"
                              tabIndex={addMenuRovingIdx === 0 ? 0 : -1}
                              className="addmenu-opt group"
                              disabled={attachmentLimitReached}
                              onKeyDown={(e) => onAddMenuOptionKey(e, 0)}
                              onClick={() => {
                                closeAddMenu();
                                fileInputRef.current?.click();
                              }}
                            >
                              <div className="flex items-center gap-3 min-w-0">
                                <div className="w-9 h-9 rounded-xl flex items-center justify-center bg-blue-500/10 text-blue-400 border border-blue-500/20 group-hover:bg-blue-500/20 transition">
                                  <FileIcon size={18} />
                                </div>
                                <div className="min-w-0 text-left">
                                  <div className="font-semibold text-sm text-[var(--text-0)]">{t('composer.uploadFile')}</div>
                                  <div className="text-xs text-[var(--text-2)] truncate">{t('composer.uploadFileDescription')}</div>
                                </div>
                              </div>
                              <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-neutral-800 text-neutral-400 border border-neutral-700/80 shrink-0">
                                {t('composer.fileTag')}
                              </span>
                            </button>

                            {/* Option 2: Take a photo */}
                            <button
                              ref={(el) => {
                                addMenuOptionRefs.current[1] = el;
                              }}
                              type="button"
                              role="menuitem"
                              tabIndex={addMenuRovingIdx === 1 ? 0 : -1}
                              className="addmenu-opt group"
                              disabled={attachmentLimitReached}
                              onKeyDown={(e) => onAddMenuOptionKey(e, 1)}
                              onClick={handleTakePhotoClick}
                            >
                              <div className="flex items-center gap-3 min-w-0">
                                <div className="w-9 h-9 rounded-xl flex items-center justify-center bg-amber-500/10 text-amber-400 border border-amber-500/20 group-hover:bg-amber-500/20 transition">
                                  <CameraIcon />
                                </div>
                                <div className="min-w-0 text-left">
                                  <div className="font-semibold text-sm text-[var(--text-0)]">{t('composer.takePhoto')}</div>
                                  <div className="text-xs text-[var(--text-2)] truncate">{t('composer.takePhotoDescription')}</div>
                                </div>
                              </div>
                              <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-neutral-800 text-neutral-400 border border-neutral-700/80 shrink-0">
                                {t('composer.cameraTag')}
                              </span>
                            </button>

                            {/* URA services are discoverable here, but only
                                reviewed taxpayer integrations may be offered. */}
                            <button
                              ref={(el) => {
                                addMenuOptionRefs.current[2] = el;
                              }}
                              type="button"
                              role="menuitem"
                              tabIndex={addMenuRovingIdx === 2 ? 0 : -1}
                              className="addmenu-opt group"
                              onKeyDown={(e) => onAddMenuOptionKey(e, 2)}
                              onClick={() => {
                                closeAddMenu();
                                setShowConnectorsDialog(true);
                                onOpenConnectors?.();
                              }}
                            >
                              <div className="flex items-center gap-3 min-w-0">
                                <div className="w-9 h-9 rounded-xl flex items-center justify-center bg-emerald-500/10 text-emerald-400 border border-emerald-500/20 group-hover:bg-emerald-500/20 transition">
                                  <PlugIcon />
                                </div>
                                <div className="min-w-0 text-left">
                                  <div className="font-semibold text-sm text-[var(--text-0)]">{t('composer.addConnector')}</div>
                                  <div className="text-xs text-[var(--text-2)] truncate">{t('composer.connectorDescription')}</div>
                                </div>
                              </div>
                              <span className="text-[11px] font-medium px-2 py-0.5 rounded-full bg-neutral-800 text-neutral-400 border border-neutral-700/80 shrink-0">
                                {t('composer.uraManagedTag')}
                              </span>
                            </button>

                          </div>
                          <div className="lmv2-foot addmenu-foot">
                            {attachmentLimitReached
                              ? t('composer.attachLimitReached')
                              : t('composer.attachPrivacyNotice')}
                          </div>
                      </>
                    </div>
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

      {showConnectorsDialog && (
        <ModalDialog
          labelledBy="composer-connectors-title"
          className="connectors-dialog"
          onClose={closeConnectorsDialog}
        >
        <div className="connectors-dialog-content">
          <header className="lmv2-head addmenu-head">
            <div className="flex items-center gap-2">
              <div className="connectors-heading-icon" aria-hidden="true">
                <PlugIcon />
              </div>
              <h2 id="composer-connectors-title">{t('composer.connectorsTitle')}</h2>
            </div>
            <button
              type="button"
              className="dlgv2-x lmv2-x"
              onClick={closeConnectorsDialog}
              aria-label={t('common.close')}
            >
              <CloseIcon />
            </button>
          </header>

          <div className="connectors-dialog-body">
            <div className="connectors-status-card" role={connectorsError ? 'alert' : 'status'}>
              <span className="connectors-status-dot" aria-hidden="true" />
              <span id="composer-connectors-description">
                {connectorsLoading
                  ? t('composer.connectorsLoading')
                  : connectorsError
                    ? t('composer.connectorsLoadFailed')
                    : !connectorsEnabled
                      ? t('composer.connectorsDisabled')
                      : chatConnectors.length
                        ? t('composer.connectorsAvailable', { count: chatConnectors.length })
                        : t('composer.connectorUnavailable')}
              </span>
            </div>

            {chatConnectors.length > 0 && (
              <div className="connectors-options" role="group" aria-label={t('composer.connectorsTitle')}>
                {chatConnectors.map((connector) => {
                  const selected = selectedConnectorNamespaces.includes(connector.namespace);
                  return (
                    <button
                      key={connector.namespace}
                      type="button"
                      className={`connectors-option${selected ? ' is-selected' : ''}`}
                      aria-pressed={selected}
                      onClick={() => onToggleConnector?.(connector.namespace)}
                    >
                      <span className="connectors-option-icon" aria-hidden="true"><PlugIcon /></span>
                      <span className="connectors-option-copy">
                        <span className="connectors-option-name">{connector.label}</span>
                        {connector.description && (
                          <span className="connectors-option-description">{connector.description}</span>
                        )}
                        <span className="connectors-option-meta">
                          {t('composer.connectorReadOnly')} · {t('composer.connectorOperationCount', { count: connector.operation_count })}
                        </span>
                      </span>
                      <span className="connectors-option-action">
                        {selected ? t('composer.connectorSelected') : t('composer.connectorAdd')}
                      </span>
                    </button>
                  );
                })}
              </div>
            )}

            <section className="connectors-detail-card" aria-labelledby="composer-connector-use-title">
              <div className="connectors-detail-heading">
                <PlugIcon size={16} />
                <h3 id="composer-connector-use-title">{t('composer.connectorUseTitle')}</h3>
              </div>
              <p>{t('composer.connectorUseDescription')}</p>
            </section>

            <aside className="connectors-safety-note" role="note">
              <h3>{t('composer.connectorSafetyTitle')}</h3>
              <p>{t('composer.connectorSafetyDescription')}</p>
            </aside>
          </div>

          <footer className="connectors-dialog-footer">
            <p>{t('composer.connectorManagedDescription')}</p>
            <button
              type="button"
              className="connectors-close-button"
              onClick={closeConnectorsDialog}
            >
              {t('common.close')}
            </button>
          </footer>
        </div>
      </ModalDialog>
      )}

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
