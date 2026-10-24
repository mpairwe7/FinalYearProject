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
import {
  connectorMonogram,
  fallbackConnectorLabel,
  type ChatConnector,
  type ConnectorAuthPayload,
} from '../lib/connectors';

interface ChatInputProps {
  message: string;
  isLoading: boolean;
  isRecording: boolean;
  isTransitioning: boolean;
  speechUnavailable: boolean;
  speechState: string;
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
     by itself, so the capability survives without the controls.

     Only *entering* is here. Once voice mode is on, its dock (VoiceModeDock)
     takes this composer's place and carries the way out. */
  onEnterVoiceMode?: () => void;
  voiceModeDisabled?: boolean;
  /* A one-line result from the last dictation attempt, shown in the hint slot.
     Without it, dictation that heard nothing was a dead end: the recording
     panel closed, the composer stayed empty, and nothing said why. */
  dictationNotice?: string | null;
  /** Abort an in-flight reply. When set, the primary slot becomes Stop while loading. */
  onStop?: () => void;
  /** Live audio frequency levels [0..1] for responsive waveform */
  audioLevels?: number[];
  chatConnectors?: ChatConnector[];
  connectorsEnabled?: boolean;
  /** The deployment has no chat connectors switched on: leave "Add a connector" out of the menu. */
  connectorsHidden?: boolean;
  connectorsLoading?: boolean;
  connectorsError?: boolean;
  selectedConnectorNamespaces?: string[];
  onOpenConnectors?: () => void;
  onToggleConnector?: (namespace: string) => void;
  onAuthenticateConnector?: (namespace: string, payload: ConnectorAuthPayload) => Promise<void>;
  /** Overrides the default placeholder (a live chat with an officer: "Message the officer…"). */
  placeholder?: string;
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
  onMessageChange,
  onSend,
  onMicClick,
  onCancelRecording,
  onFocus,
  attachments,
  onAttachFiles,
  onRemoveAttachment,
  onInspectAttachment,
  onEnterVoiceMode,
  voiceModeDisabled,
  dictationNotice,
  onStop,
  audioLevels,
  chatConnectors = [],
  connectorsEnabled = false,
  connectorsHidden = false,
  placeholder,
  connectorsLoading = false,
  connectorsError = false,
  selectedConnectorNamespaces = [],
  onOpenConnectors,
  onToggleConnector,
  onAuthenticateConnector,
}: ChatInputProps) {
  const t = useTranslation();
  const [isDragging, setIsDragging] = useState(false);
  const [showAttachMenu, setShowAttachMenu] = useState(false);
  const [showConnectorsDialog, setShowConnectorsDialog] = useState(false);
  const [authConnectorNamespace, setAuthConnectorNamespace] = useState<string | null>(null);
  const [authTab, setAuthTab] = useState<'login' | 'signup'>('login');
  const [authIdentifier, setAuthIdentifier] = useState('');
  const [authPassword, setAuthPassword] = useState('');
  const [authEntityName, setAuthEntityName] = useState('');
  const [authEmail, setAuthEmail] = useState('');
  const [authCategory, setAuthCategory] = useState<'INDIVIDUAL' | 'COMPANY'>('COMPANY');
  const [authTin, setAuthTin] = useState('');
  const [authDistrict, setAuthDistrict] = useState('Kampala');
  const [authLinkAll, setAuthLinkAll] = useState(true);
  const [authSubmitting, setAuthSubmitting] = useState(false);
  const [authError, setAuthError] = useState<string | null>(null);
  const [isCameraActive, setIsCameraActive] = useState(false);
  const attachMenuRef = useRef<HTMLDivElement>(null);
  const dragCounterRef = useRef(0);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const cameraInputRef = useRef<HTMLInputElement>(null);
  const isUploading = attachments?.some((a) => a.status === 'uploading') ?? false;
  const attachmentLimitReached = (attachments?.length ?? 0) >= MAX_ATTACHMENTS;
  // One chip per chosen service, whether or not the catalog has described it
  // yet: a choice restored from an earlier visit is still sent with every
  // message, so it must never be invisible.
  const selectedConnectors = selectedConnectorNamespaces.map((namespace) => {
    const connector = chatConnectors.find((item) => item.namespace === namespace);
    return {
      namespace,
      label: connector?.label ?? fallbackConnectorLabel(namespace),
      mode: connector?.mode,
      unavailable: connector?.status === 'unavailable',
      account: connector?.account ?? null,
    };
  });

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
    setAuthConnectorNamespace(null);
    setAuthError(null);
    setShowConnectorsDialog(false);
    window.requestAnimationFrame(() => addBtnRef.current?.focus());
  };

  const openConnectorAuth = (namespace: string, initialTab: 'login' | 'signup' = 'login') => {
    setAuthConnectorNamespace(namespace);
    setAuthTab(initialTab);
    setAuthError(null);
  };

  const handleConnectorAuthSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!authConnectorNamespace || !onAuthenticateConnector) return;
    setAuthSubmitting(true);
    setAuthError(null);
    try {
      if (authTab === 'login') {
        await onAuthenticateConnector(authConnectorNamespace, {
          action: 'login',
          identifier: authIdentifier,
          password: authPassword,
          link_all: authLinkAll,
        });
      } else {
        await onAuthenticateConnector(authConnectorNamespace, {
          action: 'signup',
          entity_name: authEntityName,
          email: authEmail,
          password: authPassword,
          category: authCategory,
          tin: authTin,
          district: authDistrict,
          link_all: authLinkAll,
        });
      }
      setAuthConnectorNamespace(null);
      setAuthPassword('');
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : t('composer.connectorsLoadFailed'));
    } finally {
      setAuthSubmitting(false);
    }
  };

  const handleDisconnectConnectorAccount = async (namespace: string) => {
    if (!onAuthenticateConnector) return;
    setAuthSubmitting(true);
    setAuthError(null);
    try {
      await onAuthenticateConnector(namespace, { action: 'disconnect' });
    } catch (err) {
      setAuthError(err instanceof Error ? err.message : t('composer.connectorsLoadFailed'));
    } finally {
      setAuthSubmitting(false);
    }
  };

  const activeAuthConnector = authConnectorNamespace
    ? chatConnectors.find((c) => c.namespace === authConnectorNamespace) ?? null
    : null;

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
    (idx) => (idx === 2 ? !connectorsHidden : !attachmentLimitReached),
  );
  const addMenuRovingIdx =
    addMenuFocusIdx !== null && addMenuOptionIndices.includes(addMenuFocusIdx)
      ? addMenuFocusIdx
      : addMenuOptionIndices[0];

  // The menu is a native modal <dialog> (ModalDialog), which owns Escape, the
  // Tab trap, inertness behind it and returning focus. What it cannot know is
  // which row is the roving one, so that row is focused as the menu opens.
  useEffect(() => {
    if (!showAttachMenu) return;
    addMenuOptionRefs.current[addMenuRovingIdx]?.focus();
  }, [showAttachMenu, addMenuRovingIdx]);

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
  // Dictation only: the checkmark sends nothing — it stops the recording and
  // drops the transcript into the composer for you to edit, and its name and
  // hint say so. (This panel used to serve voice mode too, where the same
  // checkmark sent the turn; voice mode now has its own dock.)
  if (isRecording) {
    const confirmLabel = t('composer.stopAndInsert');
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
        <p className="composer-hint composer-hint-rec">{t('composer.recHintDictation')}</p>
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
                  {a.previewUrl ? (
                    // eslint-disable-next-line @next/next/no-img-element -- a local blob: preview, not a remote asset
                    <img className="attachment-thumb" src={a.previewUrl} alt="" />
                  ) : (
                    <FileIcon />
                  )}
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
              <span
                key={connector.namespace}
                className={`composer-connector-chip${connector.unavailable ? ' is-unavailable' : ''}`}
              >
                <PlugIcon />
                <span>
                  {connector.label}
                  {connector.account?.entity_name ? ` · ${connector.account.entity_name}` : ''}
                </span>
                {connector.mode === 'simulation' && (
                  <span className="composer-connector-demo" title={t('composer.connectorDemo')}>
                    {t('composer.connectorDemoShort')}
                  </span>
                )}
                {connector.unavailable && (
                  <span className="composer-connector-down" title={t('composer.connectorUnavailableHint')}>
                    {t('composer.connectorUnavailableTag')}
                  </span>
                )}
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
          placeholder={placeholder ?? t('composer.placeholder')}
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
              <div className="addmenu-anchor" ref={attachMenuRef}>
                <button
                  ref={addBtnRef}
                  type="button"
                  className={`composer-circle-btn add-circle-btn${showAttachMenu ? ' is-active' : ''}`}
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
                  <PlusIcon size={18} />
                </button>

                {showAttachMenu && (
                  // A native modal <dialog>, in the browser's top layer. The
                  // earlier overlay lived inside the composer's stacking
                  // context, and once a conversation had messages their text
                  // and buttons painted over the panel.
                  <ModalDialog
                    labelledBy="composer-attach-title"
                    className="addmenu-dialog"
                    onClose={closeAddMenu}
                  >
                    <div ref={addMenuPanelRef} className="addmenu-panel">
                      <div className="lmv2-head addmenu-head">
                        <div className="addmenu-head-title">
                          <span className="addmenu-head-icon" aria-hidden="true">
                            <PlusIcon size={14} />
                          </span>
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
                        {/* Upload a file or screenshot */}
                        <button
                          ref={(el) => {
                            addMenuOptionRefs.current[0] = el;
                          }}
                          type="button"
                          role="menuitem"
                          tabIndex={addMenuRovingIdx === 0 ? 0 : -1}
                          className="addmenu-opt"
                          disabled={attachmentLimitReached}
                          onKeyDown={(e) => onAddMenuOptionKey(e, 0)}
                          onClick={() => {
                            closeAddMenu();
                            fileInputRef.current?.click();
                          }}
                        >
                          <span className="addmenu-opt-icon is-file" aria-hidden="true">
                            <FileIcon size={18} />
                          </span>
                          <span className="addmenu-opt-copy">
                            <span className="addmenu-opt-title">{t('composer.uploadFile')}</span>
                            <span className="addmenu-opt-desc">{t('composer.uploadFileDescription')}</span>
                          </span>
                          <span className="addmenu-opt-tag">{t('composer.fileTag')}</span>
                        </button>

                        {/* Take a photo */}
                        <button
                          ref={(el) => {
                            addMenuOptionRefs.current[1] = el;
                          }}
                          type="button"
                          role="menuitem"
                          tabIndex={addMenuRovingIdx === 1 ? 0 : -1}
                          className="addmenu-opt"
                          disabled={attachmentLimitReached}
                          onKeyDown={(e) => onAddMenuOptionKey(e, 1)}
                          onClick={handleTakePhotoClick}
                        >
                          <span className="addmenu-opt-icon is-camera" aria-hidden="true">
                            <CameraIcon />
                          </span>
                          <span className="addmenu-opt-copy">
                            <span className="addmenu-opt-title">{t('composer.takePhoto')}</span>
                            <span className="addmenu-opt-desc">{t('composer.takePhotoDescription')}</span>
                          </span>
                          <span className="addmenu-opt-tag">{t('composer.cameraTag')}</span>
                        </button>

                        {/* URA services are discoverable here, but only
                            reviewed taxpayer integrations may be offered —
                            and not at all where none are switched on, so the
                            menu has no entry that only leads to "not enabled". */}
                        {!connectorsHidden && (
                        <button
                          ref={(el) => {
                            addMenuOptionRefs.current[2] = el;
                          }}
                          type="button"
                          role="menuitem"
                          tabIndex={addMenuRovingIdx === 2 ? 0 : -1}
                          className="addmenu-opt"
                          onKeyDown={(e) => onAddMenuOptionKey(e, 2)}
                          onClick={() => {
                            closeAddMenu();
                            setShowConnectorsDialog(true);
                            onOpenConnectors?.();
                          }}
                        >
                          <span className="addmenu-opt-icon is-service" aria-hidden="true">
                            <PlugIcon />
                          </span>
                          <span className="addmenu-opt-copy">
                            <span className="addmenu-opt-title">{t('composer.addConnector')}</span>
                            <span className="addmenu-opt-desc">{t('composer.connectorDescription')}</span>
                          </span>
                          <span className="addmenu-opt-tag">
                            {selectedConnectors.length > 0
                              ? t('composer.connectorsSelectedCount', { count: selectedConnectors.length })
                              : t('composer.uraManagedTag')}
                          </span>
                        </button>
                        )}
                      </div>
                      <div className="lmv2-foot addmenu-foot">
                        {attachmentLimitReached
                          ? t('composer.attachLimitReached')
                          : t('composer.attachPrivacyNotice')}
                      </div>
                    </div>
                  </ModalDialog>
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
          ) : canSend || !onEnterVoiceMode ? (
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
              className="composer-circle-btn voicemode-circle-btn"
              onClick={onEnterVoiceMode}
              disabled={voiceModeDisabled}
              data-testid="composer-voicemode"
              aria-label={t('composer.voiceEnter')}
              data-tip={t('composer.voiceEnter')}
            >
              <VoiceWaveIcon />
            </button>
          )}
        </div>
      </div>
      {/* A dictation result takes the slot while it is there: it is about the
          thing that just happened, so it outranks a standing disclaimer.
          role=status announces it without stealing focus from the composer. */}
      <p className={`composer-hint${dictationNotice ? ' composer-hint-notice' : ''}`} role="status">
        {dictationNotice ? dictationNotice : t('composer.disclaimer')}
      </p>

      {showConnectorsDialog && (
        <ModalDialog
          labelledBy="composer-connectors-title"
          className="connectors-dialog"
          onClose={closeConnectorsDialog}
        >
          <div className="connectors-dialog-content">
            <header className="lmv2-head addmenu-head connectors-head">
              <div className="addmenu-head-title">
                <span className="connectors-heading-icon" aria-hidden="true">
                  <PlugIcon />
                </span>
                <h2 id="composer-connectors-title">{t('composer.connectorsTitle')}</h2>
                {selectedConnectors.length > 0 && (
                  <span className="connectors-count" aria-live="polite">
                    {t('composer.connectorsSelectedCount', { count: selectedConnectors.length })}
                  </span>
                )}
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
              <p className="connectors-intro">{t('composer.connectorsIntro')}</p>
              {/* Said before anything is chosen, not after: who receives what is
                  part of agreeing to a lookup (Data Protection and Privacy Act
                  2019 — a person is told the recipients of their data). */}
              <p className="connectors-data-shared">{t('composer.connectorDataShared')}</p>

              <div
                className="connectors-status-card"
                data-state={
                  connectorsLoading
                    ? 'loading'
                    : connectorsError
                      ? 'error'
                      : !connectorsEnabled || chatConnectors.length === 0
                        ? 'empty'
                        : 'ready'
                }
                role={connectorsError ? 'alert' : 'status'}
              >
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
                {connectorsError && onOpenConnectors && (
                  <button
                    type="button"
                    className="connectors-retry"
                    onClick={() => onOpenConnectors()}
                    disabled={connectorsLoading}
                  >
                    {t('composer.connectorsRetry')}
                  </button>
                )}
              </div>

              {activeAuthConnector ? (
                <div className="connectors-auth-panel" data-testid="connector-auth-panel">
                  <div className="connectors-auth-top">
                    <button
                      type="button"
                      className="connectors-auth-back"
                      onClick={() => {
                        setAuthConnectorNamespace(null);
                        setAuthError(null);
                      }}
                    >
                      ← {t('composer.connectorAuthBack')}
                    </button>
                    <div className="connectors-auth-target">
                      <span className="connectors-option-icon" aria-hidden="true">
                        {connectorMonogram(activeAuthConnector.label)}
                      </span>
                      <div>
                        <div className="connectors-option-top">
                          <span className="connectors-option-name">{activeAuthConnector.label}</span>
                          {activeAuthConnector.mode === 'simulation' && (
                            <span className="connectors-badge is-demo">{t('composer.connectorDemo')}</span>
                          )}
                        </div>
                        <p className="connectors-auth-subtitle">{t('composer.connectorAuthSubtitle')}</p>
                      </div>
                    </div>
                  </div>

                  <div className="connectors-auth-tabs" role="tablist">
                    <button
                      type="button"
                      role="tab"
                      aria-selected={authTab === 'login'}
                      className={`connectors-auth-tab${authTab === 'login' ? ' is-active' : ''}`}
                      onClick={() => {
                        setAuthTab('login');
                        setAuthError(null);
                      }}
                    >
                      {t('composer.connectorAuthSignInTab')}
                    </button>
                    <button
                      type="button"
                      role="tab"
                      aria-selected={authTab === 'signup'}
                      className={`connectors-auth-tab${authTab === 'signup' ? ' is-active' : ''}`}
                      onClick={() => {
                        setAuthTab('signup');
                        setAuthError(null);
                      }}
                    >
                      {t('composer.connectorAuthSignUpTab')}
                    </button>
                  </div>

                  {authError && (
                    <div className="connectors-auth-error" role="alert">
                      {authError}
                    </div>
                  )}

                  {authTab === 'login' && (activeAuthConnector.demo_accounts?.length ?? 0) > 0 && (
                    <div className="connectors-auth-presets">
                      <span className="connectors-auth-presets-label">
                        {t('composer.connectorAuthPresetsLabel')}:
                      </span>
                      <div className="connectors-auth-presets-list">
                        {activeAuthConnector.demo_accounts?.map((preset) => (
                          <button
                            key={preset.tin}
                            type="button"
                            className="connectors-auth-preset-chip"
                            onClick={() => {
                              setAuthIdentifier(preset.tin);
                              setAuthPassword('demo1234');
                              setAuthError(null);
                            }}
                          >
                            <strong>{preset.entity_name}</strong>
                            <span>TIN {preset.tin} · {preset.brn}</span>
                          </button>
                        ))}
                      </div>
                    </div>
                  )}

                  <form className="connectors-auth-form" onSubmit={handleConnectorAuthSubmit}>
                    {authTab === 'login' ? (
                      <>
                        <label className="connectors-auth-field">
                          <span>{t('composer.connectorAuthIdentifierLabel')}</span>
                          <input
                            type="text"
                            required
                            value={authIdentifier}
                            onChange={(e) => setAuthIdentifier(e.target.value)}
                            placeholder={t('composer.connectorAuthIdentifierPlaceholder')}
                            data-testid="connector-auth-identifier"
                          />
                        </label>
                        <label className="connectors-auth-field">
                          <span>{t('composer.connectorAuthPasswordLabel')}</span>
                          <input
                            type="password"
                            autoComplete="current-password"
                            required
                            minLength={4}
                            value={authPassword}
                            onChange={(e) => setAuthPassword(e.target.value)}
                            placeholder={t('composer.connectorAuthPasswordPlaceholder')}
                            data-testid="connector-auth-password"
                          />
                        </label>
                      </>
                    ) : (
                      <>
                        <div className="connectors-auth-grid">
                          <label className="connectors-auth-field">
                            <span>{t('composer.connectorAuthNameLabel')}</span>
                            <input
                              type="text"
                              required
                              minLength={2}
                              value={authEntityName}
                              onChange={(e) => setAuthEntityName(e.target.value)}
                              placeholder={t('composer.connectorAuthNamePlaceholder')}
                              data-testid="connector-signup-name"
                            />
                          </label>
                          <label className="connectors-auth-field">
                            <span>{t('composer.connectorAuthCategoryLabel')}</span>
                            <select
                              value={authCategory}
                              onChange={(e) =>
                                setAuthCategory(e.target.value === 'COMPANY' ? 'COMPANY' : 'INDIVIDUAL')
                              }
                              data-testid="connector-signup-category"
                            >
                              <option value="COMPANY">{t('composer.connectorAuthCategoryCompany')}</option>
                              <option value="INDIVIDUAL">{t('composer.connectorAuthCategoryIndividual')}</option>
                            </select>
                          </label>
                        </div>
                        <div className="connectors-auth-grid">
                          <label className="connectors-auth-field">
                            <span>{t('composer.connectorAuthEmailLabel')}</span>
                            <input
                              type="email"
                              required
                              value={authEmail}
                              onChange={(e) => setAuthEmail(e.target.value)}
                              placeholder={t('composer.connectorAuthEmailPlaceholder')}
                              data-testid="connector-signup-email"
                            />
                          </label>
                          <label className="connectors-auth-field">
                            <span>{t('composer.connectorAuthDistrictLabel')}</span>
                            <input
                              type="text"
                              value={authDistrict}
                              onChange={(e) => setAuthDistrict(e.target.value)}
                              placeholder="Kampala"
                            />
                          </label>
                        </div>
                        <div className="connectors-auth-grid">
                          <label className="connectors-auth-field">
                            <span>{t('composer.connectorAuthTinLabel')}</span>
                            <input
                              type="text"
                              pattern="\d{10}"
                              maxLength={10}
                              value={authTin}
                              onChange={(e) => setAuthTin(e.target.value)}
                              placeholder={t('composer.connectorAuthTinPlaceholder')}
                              data-testid="connector-signup-tin"
                            />
                          </label>
                          <label className="connectors-auth-field">
                            <span>{t('composer.connectorAuthPasswordLabel')}</span>
                            <input
                              type="password"
                              autoComplete="new-password"
                              required
                              minLength={6}
                              value={authPassword}
                              onChange={(e) => setAuthPassword(e.target.value)}
                              placeholder={t('composer.connectorAuthPasswordPlaceholder')}
                              data-testid="connector-signup-password"
                            />
                          </label>
                        </div>
                      </>
                    )}

                    <label className="connectors-auth-checkbox">
                      <input
                        type="checkbox"
                        checked={authLinkAll}
                        onChange={(e) => setAuthLinkAll(e.target.checked)}
                      />
                      <span>{t('composer.connectorAuthLinkAll')}</span>
                    </label>

                    <div className="connectors-auth-actions">
                      <button
                        type="submit"
                        className="connectors-auth-submit"
                        disabled={authSubmitting}
                        data-testid="connector-auth-submit"
                      >
                        {authSubmitting
                          ? t('composer.connectorAuthSubmitting')
                          : authTab === 'login'
                            ? t('composer.connectorAuthSubmitSignIn')
                            : t('composer.connectorAuthSubmitSignUp')}
                      </button>
                    </div>
                  </form>
                </div>
              ) : (
                <>
                  {authError && (
                    <div className="connectors-auth-error" role="alert">
                      {authError}
                    </div>
                  )}
                  {chatConnectors.length > 0 && (
                    <div className="connectors-options" role="group" aria-label={t('composer.connectorsTitle')}>
                      {chatConnectors.map((connector) => {
                        const selected = selectedConnectorNamespaces.includes(connector.namespace);
                        const unavailable = connector.status === 'unavailable';
                        const operations = connector.operations ?? [];
                        const shown = operations.slice(0, 3).map((operation) => operation.title);
                        const more = operations.length - shown.length;
                        const summary = connector.summary || connector.description;
                        const requiresAuth =
                          Boolean(onAuthenticateConnector) &&
                          Boolean(connector.requires_account_link) &&
                          !connector.account;
                        return (
                          <div key={connector.namespace} className="connectors-option-wrap">
                            <button
                              type="button"
                              className={`connectors-option${selected ? ' is-selected' : ''}${unavailable ? ' is-unavailable' : ''}`}
                              aria-pressed={selected}
                              disabled={unavailable && !selected}
                              onClick={() => {
                                if (requiresAuth && !selected) {
                                  openConnectorAuth(connector.namespace, 'login');
                                } else {
                                  onToggleConnector?.(connector.namespace);
                                }
                              }}
                            >
                              <span className="connectors-option-icon" aria-hidden="true">
                                {connectorMonogram(connector.label)}
                              </span>
                              <span className="connectors-option-copy">
                                <span className="connectors-option-top">
                                  <span className="connectors-option-name">{connector.label}</span>
                                  {connector.mode === 'simulation' && (
                                    <span className="connectors-badge is-demo">{t('composer.connectorDemo')}</span>
                                  )}
                                  {connector.mode === 'live' && (
                                    <span className="connectors-badge is-live">{t('composer.connectorLive')}</span>
                                  )}
                                  {unavailable && (
                                    <span className="connectors-badge is-down">{t('composer.connectorUnavailableTag')}</span>
                                  )}
                                </span>
                                {connector.account && (
                                  <span className="connectors-account-pill">
                                    {t('composer.connectorConnectedAs', {
                                      name: connector.account.entity_name,
                                      tin: connector.account.tin,
                                    })}
                                  </span>
                                )}
                                {summary && <span className="connectors-option-description">{summary}</span>}
                                {shown.length > 0 ? (
                                  <span className="connectors-option-meta">
                                    {t('composer.connectorOperations')}: {shown.join(' · ')}
                                    {more > 0 ? ` · ${t('composer.connectorsMore', { count: more })}` : ''}
                                  </span>
                                ) : (
                                  <span className="connectors-option-meta">
                                    {t('composer.connectorReadOnly')} ·{' '}
                                    {t('composer.connectorOperationCount', { count: connector.operation_count })}
                                  </span>
                                )}
                                {unavailable && (
                                  <span className="connectors-option-meta is-warn">
                                    {t('composer.connectorUnavailableHint')}
                                  </span>
                                )}
                              </span>
                              <span className="connectors-option-action">
                                {selected ? <CheckIcon /> : <PlusIcon size={14} />}
                                <span>
                                  {selected
                                    ? t('composer.connectorSelected')
                                    : requiresAuth
                                      ? t('composer.connectorSignInToAdd')
                                      : t('composer.connectorAdd')}
                                </span>
                              </span>
                            </button>
                            {onAuthenticateConnector && connector.account && (
                              <div className="connectors-account-bar">
                                <button
                                  type="button"
                                  className="connectors-account-link-btn"
                                  disabled={authSubmitting}
                                  onClick={() => openConnectorAuth(connector.namespace, 'login')}
                                >
                                  {t('composer.connectorSwitchAccount')}
                                </button>
                                <button
                                  type="button"
                                  className="connectors-account-link-btn is-danger"
                                  disabled={authSubmitting}
                                  onClick={() => void handleDisconnectConnectorAccount(connector.namespace)}
                                >
                                  {t('composer.connectorDisconnect')}
                                </button>
                              </div>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </>
              )}

              <details className="connectors-details">
                <summary>{t('composer.connectorsDetails')}</summary>
                <p>{t('composer.connectorUseDescription')}</p>
                <p>
                  <strong>{t('composer.connectorSafetyTitle')}.</strong> {t('composer.connectorSafetyDescription')}
                </p>
              </details>
            </div>

            <footer className="connectors-dialog-footer">
              <p>{t('composer.connectorManagedDescription')}</p>
              <div className="connectors-footer-actions">
                {selectedConnectors.length > 0 && (
                  <button
                    type="button"
                    className="connectors-clear"
                    onClick={() => selectedConnectors.forEach((c) => onToggleConnector?.(c.namespace))}
                  >
                    {t('composer.connectorsClear')}
                  </button>
                )}
                <button type="button" className="connectors-close-button" onClick={closeConnectorsDialog}>
                  {t('composer.connectorsDone')}
                </button>
              </div>
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
