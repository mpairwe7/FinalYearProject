import { officerHoursFrom, type OfficerHours } from '../lib/turnMeta';
import { create } from 'zustand';
import { conversationTtlMs, resetChatSession } from '../lib/chatSession';
import { normalizeLocale } from '../lib/locales';
import { provisionalTitle } from '../lib/chatTitle';
import type { DocumentAnalysisData } from '../lib/attachments';
import {
  sanitizeConnectorSelection,
  sanitizeConnectorUses,
  parseConnectorNotices,
  type ConnectorNotice,
  type ConnectorUse,
} from '../lib/connectors';

export interface Citation {
  ref: string;
  source: string;
  page?: string;
  section?: string;
  passage?: string;
  url?: string;
  effective_date?: string;
  title?: string;
}

/** A document attached to a user turn (analysed via /v1/documents/analyze). */
export interface ChatAttachment {
  /** Backend document id — keys the PDF analysis report download. */
  id: string;
  name: string;
  docType?: string;
  analysis?: DocumentAnalysisData;
}

export interface ContextResource {
  id?: string;
  title: string;
  type: 'downloadable_form' | 'online_form' | 'statutory_source' | 'guide' | string;
  format?: 'xlsx' | 'pdf' | 'docx' | 'web' | string;
  size?: string;
  url: string;
  description?: string;
  citation?: string;
  tax_head?: string;
  checklist?: string[];
  is_verified?: boolean;
  verification_badge?: string;
  effective_year?: string;
  source_domain?: string;
}

export interface WorkflowStepSummary {
  id: string;
  title: string;
  status: 'completed' | 'current' | 'pending';
  slot?: string;
  ui_widget?: string;
  options?: string[];
  portal_action?: { label: string; url: string; selector?: string } | null;
  resources?: ContextResource[];
}

export interface WorkflowPrerequisiteItem {
  id: string;
  label: string;
  status: 'satisfied' | 'missing' | 'pending' | string;
  slot?: string;
  remediation_workflow?: string;
  remediation_action?: string;
}

export interface WorkflowFormDraftField {
  box: string;
  label: string;
  slot: string;
  status: 'filled' | 'pending' | 'attention_needed' | string;
  display_value: string;
  masked?: boolean;
}

export interface WorkflowFormDraft {
  protocol?: string;
  surface_id?: string;
  form_code: string;
  title: string;
  completion_pct: number;
  filled_count?: number;
  total_fields?: number;
  ready_for_portal?: boolean;
  fields: WorkflowFormDraftField[];
}

export interface WorkflowJourneyGps {
  workflow_id: string;
  active_goal: string;
  step_index: number;
  total_steps: number;
  completion_pct: number;
  is_paused_for_tangent?: boolean;
  tangent_count?: number;
  parked_step_prompt?: string;
  resume_action?: string;
  resume_code?: string;
  parent_goals?: { workflow_id: string; step_idx: number }[];
}

export interface WorkflowScaffolding {
  step_id?: string;
  slot?: string;
  failed_attempts: number;
  level?: string;
  hint: string;
  examples?: string[];
  helper_actions?: string[];
}

export interface WorkflowStepUpAuth {
  required: boolean;
  mode: string;
  reason: string;
  login_url?: string;
  register_workflow_id?: string;
  register_action?: string;
  remediation_workflow_id?: string;
  remediation_action?: string;
  preserves_slots?: boolean;
  preserved_slot_count?: number;
}

export interface WorkflowState {
  id: string;
  name: string;
  status: 'active' | 'completed' | 'cancelled';
  current_step_idx?: number;
  step_index?: number;
  total_steps?: number;
  step_id?: string;
  step_title?: string;
  ui_widget?: 'options' | 'boolean' | 'text' | 'number' | 'portal_action' | string;
  options?: string[];
  option_labels?: string[];
  portal_action?: { label: string; url: string; selector?: string } | null;
  all_steps?: WorkflowStepSummary[];
  resources?: ContextResource[];
  filled_slots?: string[];
  masked_slots?: string[];
  pending_slot?: string;
  completed?: boolean;
  prerequisites?: WorkflowPrerequisiteItem[];
  form_draft?: WorkflowFormDraft;
  journey_gps?: WorkflowJourneyGps;
  scaffolding?: WorkflowScaffolding | null;
  step_up_auth?: WorkflowStepUpAuth | null;
  resume_code?: string;
}

export interface ObjectiveHypothesis {
  objective_id: string;
  title: string;
  domain: string;
  confidence?: number;
  workflow_id: string;
  workflow_name?: string;
  confirm_action: string;
  prerequisites_summary?: string;
}

export interface PortalFieldSpecItem {
  name: string;
  label: string;
  input_type: 'text' | 'number' | 'select' | 'date' | 'email' | 'tel' | string;
  required: boolean;
  placeholder?: string;
  helper_text?: string;
  sensitive?: boolean;
  value?: string;
  options?: { value: string; label: string }[];
  validation_error?: string;
}

export interface PortalDisplayField {
  name: string;
  label: string;
  value: string;
  raw_value?: string;
  sensitive?: boolean;
}

export interface PortalCopilotPayload {
  card_type:
    | 'inline_form'
    | 'confirmation_required'
    | 'step_up_login'
    | 'statutory_copilot_handoff'
    | 'stuck_portal_assistance'
    | 'executed_result'
    | 'access_denied'
    | 'copilot_launch_banner'
    | 'screenshot_copilot_handoff'
    | 'error_diagnosis'
    | string;
  task_id?: string;
  portal_key?: string;
  portal_name?: string;
  title?: string;
  tier?: string;
  requires_auth?: boolean;
  is_authenticated?: boolean;
  statutory_citation?: string;
  description?: string;
  missing_fields?: string[];
  field_errors?: Record<string, string>;
  fields?: PortalFieldSpecItem[];
  submit_label?: string;
  verification_prompt?: string;
  verification_warning?: string;
  reversibility_notice?: string;
  display_fields?: PortalDisplayField[];
  displayed_fields_sha256?: string;
  high_value_requires_ack?: boolean;
  allow_label?: string;
  disallow_label?: string;
  edit_label?: string;
  request_state?: Record<string, unknown>;
  idempotency_key?: string;
  expires_in_seconds?: number;
  simulation_mode?: boolean;
  live?: boolean;
  mode?: 'simulation' | 'live' | string;
  result?: Record<string, unknown>;
  login_reason?: string;
  return_to_chat_url?: string;
  return_to_portal_url?: string;
  signin_chat_href?: string;
  signin_portal_href?: string;
  preserved_fields?: Record<string, string>;
  raw_preserved_fields?: Record<string, string>;
  copilot_portal_url?: string;
  open_portal_label?: string;
  dom_actions?: Array<{
    step_number: number;
    element: string;
    selector: string;
    action_type: string;
    fill_value?: string | null;
    scroll_to?: boolean;
    tooltip: string;
    security_gate: string;
  }>;
  diagnosis_summary?: string;
  diagnosis_points?: string[];
  error?: string;
  user_message?: string;
  what_needs_to_be_done?: string;
  checklist?: Array<{
    item: string;
    status: 'detected' | 'action_needed' | string;
    detail: string;
  }>;
  recommended_prompts?: string[];
  completeness_check?: {
    is_complete: boolean;
    missing_context_hints?: string[];
    clarifying_options?: string[];
  };
  error_diagnosis?: {
    error_code: string;
    root_cause: string;
    statutory_citation: string;
    resolution_steps: string[];
    remediation_task_id?: string;
  };
}

export interface ChatTurn {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  timestamp: number;
  /** The server's id for this answer in its conversation log (`turn_id` on
   *  the chat response). A rating sends it, so the audit knows which answer
   *  was rated. Absent on turns the server did not log, like the greeting. */
  serverTurnId?: string;
  /** A line from the chat itself rather than a message ("an officer has
   *  joined", "the chat has ended"). Rendered as a notice, never rated. */
  notice?: ChatNoticeKind;
  /** The officer who wrote this turn (an officer's message in a live chat),
   *  or who joined or ended it (a notice). */
  officerName?: string;
  citations?: Citation[];
  faithfulnessScore?: number | null;
  /** Interrupted/stopped replies are retained as partial text, never as finished guidance. */
  responseIncomplete?: boolean;
  /** Browser history cannot establish that an action is still current on the server. */
  restored?: boolean;
  retrievalMode?: string;
  escalationRequired?: boolean;
  escalationReason?: string;
  /** Whether officers were on duty when this turn's handoff was queued (G196). */
  officerHours?: OfficerHours;
  /** Suggested follow-up action chips offered by the assistant */
  nextActions?: string[];
  /** Active multi-step workflow status and interactive stepper state */
  workflow?: WorkflowState;
  /** Proactive objective hypothesis inferred from situational taxpayer challenges */
  objectiveHypothesis?: ObjectiveHypothesis | null;
  /** Relevant downloadable forms, online portal links, and statutory sources */
  resources?: ContextResource[];
  /** Whether this turn was answered from the offline RAG pipeline */
  offlineMode?: boolean;
  /** Documents attached to this (user) turn */
  attachments?: ChatAttachment[];
  /** A taxpayer turn the speech recogniser wrote down from what they said (voice mode). */
  spoken?: boolean;
  /**
   * Wall-clock time the assistant spent on this turn, from the moment the
   * question was sent to the moment the reply finished. Persisted so the
   * "Thought for 13.9s" note survives a reload rather than vanishing with the
   * in-flight state that produced it.
   */
  thoughtForMs?: number;
  /** Knowledge discrepancy / bug report metadata if the turn disputed previous guidance */
  discrepancyReport?: {
    id: string;
    status: string;
    frequency_count?: number;
    discrepancy_type?: string;
  };
  /** Services this answer looked things up in, and how each lookup ended. */
  connectorsUsed?: ConnectorUse[];
  /** Selected services this answer was given without, and why. */
  connectorNotices?: ConnectorNotice[];
  /** Phase 42 — URA Portal Task Execution & Inbuilt Co-Pilot card state */
  portalCopilot?: PortalCopilotPayload | null;
}

/** The chat's own notices: an officer joining, and the hand-back to the AI. */
export type ChatNoticeKind = 'officer_joined' | 'officer_left';

/** Where the live chat with an officer stands, as the server last said. */
export type OfficerChatState = 'waiting' | 'officer' | 'ended' | 'none';

/**
 * The taxpayer's side of a live chat with an officer, kept per conversation so
 * a reload resumes where it was: the case, the last thread message already
 * shown (so none is shown twice) and the state.
 */
export interface OfficerChatSync {
  ticketId: string;
  lastSeq: number;
  state: OfficerChatState;
  officerName?: string;
}

export interface Conversation {
  id: string;
  title: string;
  preview: string;
  turns: ChatTurn[];
  createdAt: number;
  updatedAt: number;
  /** Pinned threads sort above everything else in the rail and the chats page. */
  pinned?: boolean;
  /**
   * The title was typed by the user, so `deriveTitle` must stop overwriting it.
   * Without this flag a rename survives only until the next turn is saved.
   */
  titleCustom?: boolean;
  /**
   * The title is the model's summary of the opening exchange
   * (`/v1/chat/title`, see `hooks/useAutoTitle.ts`). Kept across saves like a
   * typed title; a rename still replaces it.
   */
  titleGenerated?: boolean;
  /** Active escalation ticket ID associated with this conversation */
  activeTicketId?: string;
  /** The live chat with an officer on that ticket, if one has started. */
  officerChat?: OfficerChatSync;
  /**
   * The services the taxpayer chose for this conversation. A choice is made
   * once and meant for the whole conversation, so it comes back on a switch
   * and a reload instead of silently lapsing into plain answers.
   */
  connectorNamespaces?: string[];
}

/**
 * `starting` = the mic has been asked for and has not opened yet.
 *
 * This state exists because of a reported bug, not for polish: "one has to
 * press the button twice before they can even listen". Opening a mic is slow
 * — a permission prompt the first time, then the speech engine warming up —
 * and until this state existed the button stayed on `idle` throughout, so it
 * looked untouched and invited a second press. That second press called
 * `start()` on a recognizer that was already starting, which throws
 * `InvalidStateError`, and the handler read the throw as a failure and put the
 * mic in `error`. The first press had worked; the second one broke it, and a
 * third was needed. Naming the interval is what removes the invitation.
 *
 * `processing` = recording stopped, transcription in flight. Only the
 * server-ASR dictation path reaches it; the browser Speech API returns its
 * transcript synchronously in onresult and goes straight back to `idle`.
 */
export type SpeechState =
  | 'idle'
  | 'starting'
  | 'listening'
  | 'processing'
  | 'unavailable'
  | 'error';

interface ChatStore {
  message: string;
  chat: ChatTurn[];
  speechState: SpeechState;
  locale: string;
  /**
   * True when the taxpayer picked `locale` themselves. False means auto-detect:
   * `locale` is then only a hint (the last language the assistant answered in)
   * and the backend decides from what was typed and the conversation so far.
   */
  localeExplicit: boolean;
  // Session management
  conversations: Conversation[];
  activeConversationId: string | null;
  activeTicketId: string | null;
  /** The active conversation's live chat with an officer. */
  officerChat: OfficerChatSync | null;
  /** Services selected for the active conversation (namespaces). */
  selectedConnectorNamespaces: string[];
  supportCaseOpen: boolean;
  // Actions
  setMessage: (value: string) => void;
  setSpeechState: (state: SpeechState) => void;
  /** The taxpayer picked a language. */
  setLocale: (locale: string) => void;
  /** Go back to auto-detecting the answer language. */
  setAutoDetectLocale: () => void;
  /**
   * Follow the language the backend answered in. Only `explicit_request`
   * ("answer in Luganda", typed into the chat) becomes the taxpayer's choice;
   * a detected or continued language stays a hint.
   */
  adoptResponseLocale: (locale: string, source?: string) => void;
  setActiveTicketId: (ticketId: string | null) => void;
  /** Record where the live officer chat stands (null when it is over). */
  setOfficerChat: (sync: OfficerChatSync | null) => void;
  /** Choose the services for the active conversation; kept with it. */
  setSelectedConnectorNamespaces: (namespaces: string[]) => void;
  setSupportCaseOpen: (open: boolean) => void;
  addTurns: (turns: ChatTurn[]) => void;
  updateLastTurn: (updater: (turn: ChatTurn) => ChatTurn) => void;
  reset: () => void;
  // Session actions
  ensureActiveConversationId: () => string;
  saveCurrentSession: () => void;
  createNewSession: () => void;
  switchSession: (id: string) => void;
  deleteSession: (id: string) => void;
  renameSession: (id: string, title: string) => void;
  /** Name a conversation with the model's summary title, unless the user renamed it. */
  setGeneratedTitle: (id: string, title: string) => void;
  togglePinSession: (id: string) => void;
  clearAllSessions: () => void;
  hydratePersisted: () => void;
}

function generateId(): string {
  if (typeof crypto !== 'undefined' && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

const GREETING: ChatTurn = {
  id: 'greeting-0',
  role: 'assistant',
  content: 'Hi! I can answer your questions about URA. Type or speak your question to begin.',
  timestamp: 0,
};

export function createTurn(
  role: 'user' | 'assistant',
  content: string,
  meta?: Partial<Omit<ChatTurn, 'id' | 'role' | 'content' | 'timestamp'>>,
): ChatTurn {
  return { id: generateId(), role, content, timestamp: Date.now(), ...meta };
}

const MAX_TURNS = 200;
const MAX_SESSIONS = 50;
const CHAT_STORAGE_KEY = 'ura-chat-store';
const CHAT_STORAGE_VERSION = 2;

/** The provisional title: the first user message, boiled down (lib/chatTitle). */
function deriveTitle(turns: ChatTurn[]): string {
  const first = turns.find((t) => t.role === 'user');
  if (!first) return 'New chat';
  return provisionalTitle(first.content) || 'New chat';
}

type PersistedChatState = Pick<ChatStore, 'locale' | 'conversations' | 'activeConversationId'> & {
  localeExplicit?: boolean;
  draftMessage?: string;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

function getChatStorage(): Storage | null {
  if (typeof window === 'undefined') return null;
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function sanitizeTurn(value: unknown): ChatTurn | null {
  if (!isRecord(value)) return null;
  const id = typeof value.id === 'string' && value.id ? value.id : generateId();
  const role = value.role === 'user' || value.role === 'assistant' ? value.role : null;
  const content = typeof value.content === 'string' ? value.content : null;
  if (!role || content == null) return null;

  const turn: ChatTurn = {
    id,
    role,
    content,
    timestamp: typeof value.timestamp === 'number' ? value.timestamp : 0,
    restored: true,
  };

  if (Array.isArray(value.citations)) turn.citations = value.citations as Citation[];
  if (typeof value.faithfulnessScore === 'number' || value.faithfulnessScore === null) {
    turn.faithfulnessScore = value.faithfulnessScore;
  }
  if (typeof value.retrievalMode === 'string') turn.retrievalMode = value.retrievalMode;
  if (typeof value.serverTurnId === 'string' && value.serverTurnId) turn.serverTurnId = value.serverTurnId;
  if (value.notice === 'officer_joined' || value.notice === 'officer_left') turn.notice = value.notice;
  if (typeof value.officerName === 'string' && value.officerName) turn.officerName = value.officerName.slice(0, 128);
  if (typeof value.escalationRequired === 'boolean') turn.escalationRequired = value.escalationRequired;
  if (typeof value.escalationReason === 'string') turn.escalationReason = value.escalationReason;
  const hours = officerHoursFrom({ officer_hours: value.officerHours });
  if (hours) turn.officerHours = hours;
  if (typeof value.offlineMode === 'boolean') turn.offlineMode = value.offlineMode;
  if (typeof value.responseIncomplete === 'boolean') turn.responseIncomplete = value.responseIncomplete;
  if (typeof value.thoughtForMs === 'number' && Number.isFinite(value.thoughtForMs)) {
    turn.thoughtForMs = value.thoughtForMs;
  }
  const used = sanitizeConnectorUses(value.connectorsUsed);
  if (used.length) turn.connectorsUsed = used;
  const notices = parseConnectorNotices(value.connectorNotices);
  if (notices.length) turn.connectorNotices = notices;
  const disc = value.discrepancyReport ?? (value as Record<string, unknown>).discrepancy_report;
  if (isRecord(disc) && typeof disc.id === 'string') {
    turn.discrepancyReport = {
      id: disc.id,
      status: String(disc.status || 'pending'),
      frequency_count: typeof disc.frequency_count === 'number' ? disc.frequency_count : 1,
      discrepancy_type: typeof disc.discrepancy_type === 'string' ? disc.discrepancy_type : 'outdated_law',
    };
  }
  if (Array.isArray(value.attachments)) {
    const attachments = value.attachments
      .filter(isRecord)
      .filter((a) => typeof a.id === 'string' && a.id && typeof a.name === 'string')
      .map((a) => {
        const attachment: ChatAttachment = { id: a.id as string, name: a.name as string };
        if (typeof a.docType === 'string') attachment.docType = a.docType;
        return attachment;
      });
    if (attachments.length) turn.attachments = attachments;
  }
  if (Array.isArray(value.nextActions)) {
    turn.nextActions = value.nextActions.filter((a): a is string => typeof a === 'string');
  }
  if (isRecord(value.workflow) && typeof value.workflow.id === 'string') {
    turn.workflow = value.workflow as unknown as WorkflowState;
  }
  const objHyp = value.objectiveHypothesis ?? (value as Record<string, unknown>).objective_hypothesis;
  // The card's button sends confirm_action as the taxpayer's next message in
  // one tap. Storage cannot prove which workflow a stored action names, so a
  // restored card is shown without its button; the same action comes back as
  // a next-action chip (turn_guidance puts it there), so nothing is lost.
  if (
    isRecord(objHyp) &&
    typeof objHyp.objective_id === 'string' &&
    typeof objHyp.title === 'string' &&
    typeof objHyp.workflow_id === 'string'
  ) {
    turn.objectiveHypothesis = {
      objective_id: objHyp.objective_id,
      title: objHyp.title,
      domain: typeof objHyp.domain === 'string' ? objHyp.domain : 'general',
      confidence:
        typeof objHyp.confidence === 'number' &&
        Number.isFinite(objHyp.confidence) &&
        objHyp.confidence >= 0 &&
        objHyp.confidence <= 1
          ? objHyp.confidence
          : undefined,
      workflow_id: objHyp.workflow_id,
      workflow_name: typeof objHyp.workflow_name === 'string' ? objHyp.workflow_name : undefined,
      confirm_action: '',
      prerequisites_summary:
        typeof objHyp.prerequisites_summary === 'string' ? objHyp.prerequisites_summary : undefined,
    };
  }
  if (Array.isArray(value.resources)) {
    turn.resources = value.resources.filter(isRecord) as unknown as ContextResource[];
  }

  return turn;
}

function sanitizeTurns(value: unknown): ChatTurn[] {
  if (!Array.isArray(value)) return [];
  return value.map(sanitizeTurn).filter((turn): turn is ChatTurn => Boolean(turn)).slice(-MAX_TURNS);
}

const OFFICER_CHAT_STATES: readonly OfficerChatState[] = ['waiting', 'officer', 'ended', 'none'];

function sanitizeOfficerChat(value: unknown): OfficerChatSync | null {
  if (!isRecord(value) || typeof value.ticketId !== 'string' || !value.ticketId) return null;
  const state = OFFICER_CHAT_STATES.includes(value.state as OfficerChatState)
    ? (value.state as OfficerChatState)
    : 'waiting';
  const sync: OfficerChatSync = {
    ticketId: value.ticketId,
    lastSeq: typeof value.lastSeq === 'number' && Number.isFinite(value.lastSeq) ? Math.max(0, value.lastSeq) : 0,
    state,
  };
  if (typeof value.officerName === 'string' && value.officerName) sync.officerName = value.officerName.slice(0, 128);
  return sync;
}

function sanitizeConversation(value: unknown): Conversation | null {
  if (!isRecord(value)) return null;
  const id = typeof value.id === 'string' && value.id ? value.id : null;
  if (!id) return null;

  const turns = sanitizeTurns(value.turns);
  const lastTurn = turns[turns.length - 1];
  const conversation: Conversation = {
    id,
    title: typeof value.title === 'string' && value.title ? value.title : deriveTitle(turns),
    preview:
      typeof value.preview === 'string'
        ? value.preview.slice(0, 120)
        : lastTurn?.content.slice(0, 80) ?? '',
    turns,
    createdAt: typeof value.createdAt === 'number' ? value.createdAt : 0,
    updatedAt: typeof value.updatedAt === 'number' ? value.updatedAt : 0,
  };
  if (value.pinned === true) conversation.pinned = true;
  if (value.titleCustom === true) conversation.titleCustom = true;
  if (value.titleGenerated === true) conversation.titleGenerated = true;
  if (typeof value.activeTicketId === 'string' && value.activeTicketId) conversation.activeTicketId = value.activeTicketId;
  const sync = sanitizeOfficerChat(value.officerChat);
  if (sync) conversation.officerChat = sync;
  const connectors = sanitizeConnectorSelection(value.connectorNamespaces);
  if (connectors.length) conversation.connectorNamespaces = connectors;
  return conversation;
}

/**
 * The conversations the server still remembers. It deletes a conversation
 * `CONVERSATION_TTL_DAYS` after its last turn, so an older one reopened here
 * would continue without its history (G122). A pinned conversation is kept:
 * the taxpayer chose to keep it. One stored without a time starts its clock now.
 */
function withinRetention(conversations: Conversation[], now: number = Date.now()): Conversation[] {
  const ttl = conversationTtlMs();
  return conversations
    .map((conversation) => (conversation.updatedAt > 0 ? conversation : { ...conversation, updatedAt: now }))
    .filter((conversation) => conversation.pinned || now - conversation.updatedAt < ttl);
}

function readPersistedChatState(): PersistedChatState | null {
  const storage = getChatStorage();
  if (!storage) return null;

  try {
    const raw = storage.getItem(CHAT_STORAGE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as unknown;
    const state: Record<string, unknown> | null =
      isRecord(parsed) && isRecord(parsed.state)
        ? parsed.state
        : isRecord(parsed)
          ? parsed
          : null;
    if (!state) return null;

    let conversations = Array.isArray(state.conversations)
      ? withinRetention(
          state.conversations
            .map(sanitizeConversation)
            .filter((conversation): conversation is Conversation => Boolean(conversation)),
        ).slice(0, MAX_SESSIONS)
      : [];

    let activeConversationId =
      typeof state.activeConversationId === 'string' ? state.activeConversationId : null;

    const legacyChat = sanitizeTurns(state.chat);
    if (legacyChat.length > 1 && !conversations.some((conversation) => conversation.id === activeConversationId)) {
      const id = activeConversationId ?? generateId();
      const now = Date.now();
      const lastTurn = legacyChat[legacyChat.length - 1];
      conversations = [
        {
          id,
          title: deriveTitle(legacyChat),
          preview: lastTurn?.content.slice(0, 80) ?? '',
          turns: legacyChat,
          createdAt: now,
          updatedAt: now,
        },
        ...conversations.filter((conversation) => conversation.id !== id),
      ].slice(0, MAX_SESSIONS);
      activeConversationId = id;
    }

    if (!conversations.some((conversation) => conversation.id === activeConversationId)) {
      activeConversationId = null;
    }

    const draftMessage = typeof state.draftMessage === 'string' ? state.draftMessage : '';

    return {
      locale: normalizeLocale(state.locale),
      // Stored before the flag existed: auto-detect, the default.
      localeExplicit: state.localeExplicit === true,
      conversations,
      activeConversationId,
      draftMessage,
    };
  } catch {
    return null;
  }
}

function withoutTransientCards(conversations: Conversation[]): Conversation[] {
  return conversations.map((c) => ({
    ...c,
    turns: c.turns.map((t) => (t.portalCopilot ? { ...t, portalCopilot: undefined } : t)),
  }));
}

function writePersistedChatState(state: PersistedChatState): void {
  const storage = getChatStorage();
  if (!storage) return;

  try {
    storage.setItem(
      CHAT_STORAGE_KEY,
      JSON.stringify({
        state: {
          locale: normalizeLocale(state.locale),
          localeExplicit: state.localeExplicit === true,
          conversations: withoutTransientCards(state.conversations.slice(0, MAX_SESSIONS)),
          activeConversationId: state.activeConversationId,
          draftMessage: state.draftMessage ?? '',
        },
        version: CHAT_STORAGE_VERSION,
      }),
    );
  } catch {
    // Storage can fail in private mode or under quota pressure.
  }
}

/**
 * Strip LLM chain-of-thought reasoning and normalize formatting.
 *
 * Strategy: The LLM produces two sections:
 *   1. Internal reasoning (talks about "the user", "passage [1]", "I should", etc.)
 *   2. The actual answer (addresses the user directly)
 *
 * We detect the boundary by finding where reasoning ends and the
 * direct answer begins, using signals like:
 *   - Triple newline separator (\n\n\n)
 *   - Blocks that reference internal reasoning patterns
 */

const THINKING_SIGNALS = [
  // Meta-commentary about the task
  /^(okay|alright|hmm|well|right|sure|let me|now,)/i,
  /^the user (is |want|ask)/i,
  /^i (need|should|also|will|can|have) /i,
  // Reasoning about passages/sources
  /^(looking at|checking|passage |but it|but the|it (also|doesn|mention))/i,
  /passage \[?\d/i,
  /^so,? (the|i |none|based|from)/i,
  /^since (the|none|it|this|there)/i,
  /^(therefore|however|also|additionally),? (note that|i think|i should|the user|the passage|looking)/i,
  // Self-narration about what to do
  /^(the (main|key|relevant) point|the answer|my response|i.ll |let.s )/i,
  /^(this|that) (means|is|doesn|seem|suggest|passage)/i,
  /not (list|mention|provide|explain|specify|include)/i,
];

function looksLikeThinking(block: string): boolean {
  const trimmed = block.trimStart();
  if (!trimmed) return true;
  return THINKING_SIGNALS.some((rx) => rx.test(trimmed));
}

/** Strip internal telemetry/metadata/agent trace JSON blobs and thinking blocks that leak into prose. */
export function stripTelemetryJson(text: string): string {
  if (!text) return '';
  return text
    // Strip thinking and scratchpad tags (<think>...</think>, <thought>...</thought>, etc.), including unclosed
    .replace(/<(?:think|thought|reasoning|scratchpad)[^>]*>[\s\S]*?(?:<\/(?:think|thought|reasoning|scratchpad)>|$)/gi, '')
    // Strip ```thought / ```thinking code blocks
    .replace(/```(?:thought|thinking)[\s\S]*?```/gi, '')
    .replace(/\b(?:translation|retrieval|generation|iteration|tool_call)\.(?:started|completed)\b\s*/g, '')
    .replace(/\{[^{}]*"(?:sources|workflow|retrieval_mode)"[^{}]*\{[^{}]*\}[^{}]*\}/g, '')
    .replace(/\{[^{}]*"(?:sources|retrieval_mode|faithfulness_score)"[^{}]*\}/g, '')
    .replace(/\[\s*\{\s*"type"\s*:\s*"(?:retrieval|iteration|tool_call)\.[^\]]*?\]/g, '')
    .trim();
}

export function normalizeAssistantResponse(text: string): string {
  const stripped = stripTelemetryJson(text);
  return stripped
    .replace(/\r\n?/g, '\n')
    .replace(/[ \t]+\n/g, '\n')
    // Unsmash lead-ins into first numbered item (e.g. 'steps:1.**' or 'steps: 1.')
    .replace(/(?<!\d)([;:\.!?])[ \t]*(\d{1,2})[\.\)][ \t]*(\*{0,2}[A-Za-z])/g, '$1\n\n$2. $3')
    // A sentence boundary before a step ('taxes.2.**'). A rate ('VAT 18. File') has no boundary.
    .replace(/(?<!\d)([;:\.!?])[ \t]*(\d{1,2})[.)][ \t]*(\*{0,2}[A-Za-z])/g, '$1\n\n$2. $3')
    // A word glued to a step ('template2. Enable'), not a section or a rate ('Section5', 'VAT18').
    .replace(
      /(^|[^A-Za-z])(?!(?:section|article|schedule|form|act|rule|clause|paragraph|vat|paye|wht|tin|cit|pit|ugx|efris|rate|percent)\d)([A-Za-z]{3,})([1-9]|1[0-2])[.)][ \t]*(\*{0,2}[A-Za-z])/gi,
      '$1$2.\n\n$3. $4',
    )
    // Separate closing/assistance paragraphs (e.g. 'month.For assistance' -> 'month.\n\nFor assistance')
    .replace(/([a-z0-9\)])\.\s*(?=(?:For assistance|If you (?:need|get)|Contact URA|Please note|Note:))/gi, '$1.\n\n')
    // Standardize numbered lists: "1) Item" or "1. Item" -> "1. Item"
    .replace(/(^|\n)\s*(\d+)[\.)]\s+/g, '$1$2. ')
    // Standardize unordered bullets: "*", "+", "•" -> "- "
    .replace(/(^|\n)\s*[\u2022*+]\s+/g, '$1- ')
    // Emphasize title before colon on bullet lists: "- Tax Administration: text" -> "- **Tax Administration**: text"
    .replace(/(^|\n)-\s+([A-Z][A-Za-z0-9\s/&-]{1,60}):\s+/g, '$1- **$2**: ')
    // Emphasize title before colon on numbered lists: "1. Registration: text" -> "1. **Registration**: text"
    .replace(/(^|\n)(\d+\.)\s+([A-Z][A-Za-z0-9\s/&-]{1,60}):\s+/g, '$1$2 **$3**: ')
    .replace(/([.!?])\s+(?=(Note|Important|Tip|Warning|Caution|Summary):\s)/g, '$1\n\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

export function cleanResponse(text: string): string {
  let cleaned = stripTelemetryJson(text);
  if (!cleaned) return cleaned;

  // Strip internal model scratchpad thoughts
  cleaned = cleaned.replace(/<(?:thought|think)>[\s\S]*?<\/(?:thought|think)>\s*/gi, '').trim();

  // Split into paragraph blocks
  const blocks = cleaned.split('\n\n').filter((b) => b.trim());
  if (blocks.length < 2) return normalizeAssistantResponse(cleaned);

  // If the first block isn't thinking, return as-is
  if (!looksLikeThinking(blocks[0])) return normalizeAssistantResponse(cleaned);

  // Tag each block as thinking or answer
  const tags = blocks.map((b) => looksLikeThinking(b));

  // Find the LAST contiguous run of answer blocks — that's the real response.
  // The LLM pattern is: thinking... (maybe mixed answer)... FINAL ANSWER blocks
  let lastAnswerEnd = blocks.length - 1;
  while (lastAnswerEnd >= 0 && tags[lastAnswerEnd]) lastAnswerEnd--;

  if (lastAnswerEnd < 0) return normalizeAssistantResponse(cleaned); // all thinking, return as-is

  // Actually: find the start of the final answer cluster
  let lastAnswerStart = lastAnswerEnd;
  for (let i = lastAnswerEnd - 1; i >= 0; i--) {
    if (!tags[i]) lastAnswerStart = i;
    else break;
  }

  cleaned = blocks.slice(lastAnswerStart).filter((_, i) => !tags[lastAnswerStart + i]).join('\n\n').trim();

  // Normalize *Note: disclaimers into visually separated sections
  cleaned = cleaned.replace(/\n---\n\*Note/g, '\n\n---\n\n*Note');
  cleaned = cleaned.replace(/([.!?])\s*\*Note:/g, '$1\n\n---\n\n*Note:');

  return normalizeAssistantResponse(cleaned || text.trim());
}

let draftSaveTimer: ReturnType<typeof setTimeout> | null = null;

function debouncedSaveDraft(state: ChatStore): void {
  if (draftSaveTimer) clearTimeout(draftSaveTimer);
  draftSaveTimer = setTimeout(() => {
    writePersistedChatState({
      locale: state.locale,
      localeExplicit: state.localeExplicit,
      conversations: state.conversations,
      activeConversationId: state.activeConversationId,
      draftMessage: state.message,
    });
  }, 400);
}

export const useChatStore = create<ChatStore>()((set, get) => ({
  message: '',
  chat: [GREETING],
  speechState: 'idle' as SpeechState,
  locale: 'en',
  localeExplicit: false,
  conversations: [] as Conversation[],
  activeConversationId: null as string | null,
  activeTicketId: null as string | null,
  officerChat: null as OfficerChatSync | null,
  selectedConnectorNamespaces: [] as string[],
  supportCaseOpen: false,

  setMessage: (value) => {
    set({ message: value });
    debouncedSaveDraft(get());
  },
  setSpeechState: (state) => set({ speechState: state }),
  setLocale: (locale) => {
    set({ locale: normalizeLocale(locale), localeExplicit: true });
    writePersistedChatState(get());
  },
  setAutoDetectLocale: () => {
    set({ localeExplicit: false });
    writePersistedChatState(get());
  },
  adoptResponseLocale: (locale, source) => {
    const next = normalizeLocale(locale);
    const explicit = source === 'explicit_request' ? true : get().localeExplicit;
    if (next === get().locale && explicit === get().localeExplicit) return;
    set({ locale: next, localeExplicit: explicit });
    writePersistedChatState(get());
  },
  setActiveTicketId: (ticketId) => {
    const cid = get().activeConversationId;
    if (cid) {
      set((state) => ({
        activeTicketId: ticketId,
        conversations: state.conversations.map((c) =>
          c.id === cid ? { ...c, activeTicketId: ticketId || undefined } : c
        ),
      }));
      get().saveCurrentSession();
    } else {
      set({ activeTicketId: ticketId });
    }
  },
  setOfficerChat: (sync) => {
    const cid = get().activeConversationId;
    set((state) => ({
      officerChat: sync,
      conversations: cid
        ? state.conversations.map((c) => (c.id === cid ? { ...c, officerChat: sync || undefined } : c))
        : state.conversations,
    }));
    if (cid) get().saveCurrentSession();
  },
  setSelectedConnectorNamespaces: (namespaces) => {
    const next = sanitizeConnectorSelection(namespaces);
    const cid = get().activeConversationId;
    set((state) => ({
      selectedConnectorNamespaces: next,
      conversations: cid
        ? state.conversations.map((c) =>
            c.id === cid ? { ...c, connectorNamespaces: next.length ? next : undefined } : c,
          )
        : state.conversations,
    }));
    writePersistedChatState(get());
  },
  setSupportCaseOpen: (open) => set({ supportCaseOpen: open }),

  addTurns: (turns) =>
    set((s) => {
      const updated = [...s.chat, ...turns];
      return { chat: updated.length > MAX_TURNS ? updated.slice(-MAX_TURNS) : updated };
    }),

  updateLastTurn: (updater) =>
    set((s) => {
      if (s.chat.length === 0) return s;
      const updated = [...s.chat];
      updated[updated.length - 1] = updater(updated[updated.length - 1]);
      return { chat: updated };
    }),

  reset: () => {
    set({
      message: '',
      chat: [GREETING],
      speechState: 'idle',
      locale: 'en',
      localeExplicit: false,
      activeConversationId: null,
      activeTicketId: null,
      officerChat: null,
      selectedConnectorNamespaces: [],
      supportCaseOpen: false,
    });
    writePersistedChatState(get());
  },

  /** Ensure there is a stable id for the active conversation thread. */
  ensureActiveConversationId: () => {
    const { activeConversationId } = get();
    if (activeConversationId) return activeConversationId;
    const id = generateId();
    set({ activeConversationId: id });
    return id;
  },

  /** Save the current chat as a conversation (or update existing). */
  saveCurrentSession: () => {
    const { chat, conversations, activeConversationId, activeTicketId, officerChat, selectedConnectorNamespaces } =
      get();
    // Only save if there are user messages
    if (chat.length <= 1) {
      writePersistedChatState(get());
      return;
    }
    const now = Date.now();
    const title = deriveTitle(chat);
    const lastMsg = chat[chat.length - 1];
    const preview = lastMsg.content.slice(0, 80);

    const id = activeConversationId ?? generateId();
    const conv: Conversation = { id, title, preview, turns: chat, createdAt: now, updatedAt: now };
    // The open case and its live chat belong to this conversation, so a
    // reload or a switch back resumes them.
    if (activeTicketId) conv.activeTicketId = activeTicketId;
    if (officerChat) conv.officerChat = officerChat;
    if (selectedConnectorNamespaces.length) conv.connectorNamespaces = selectedConnectorNamespaces;
    const existing = conversations.find((c) => c.id === id);

    if (existing) {
      // A title the user typed is theirs, and one the model summarised is
      // better than the first message. `deriveTitle` re-runs on every save, so
      // without this guard either lasts exactly until the next turn.
      set({
        activeConversationId: id,
        conversations: conversations.map((c) =>
          c.id === id
            ? {
                ...c,
                title: c.titleCustom || c.titleGenerated ? c.title : title,
                preview,
                turns: chat,
                updatedAt: now,
                activeTicketId: activeTicketId || undefined,
                officerChat: officerChat || undefined,
                connectorNamespaces: selectedConnectorNamespaces.length ? selectedConnectorNamespaces : undefined,
              }
            : c,
        ),
      });
    } else {
      set({
        activeConversationId: id,
        conversations: [conv, ...conversations].slice(0, MAX_SESSIONS),
      });
    }
    writePersistedChatState(get());
  },

  /** Save current, then start fresh. */
  createNewSession: () => {
    const { saveCurrentSession } = get();
    saveCurrentSession();
    set({
      chat: [GREETING],
      message: '',
      activeConversationId: null,
      activeTicketId: null,
      officerChat: null,
      selectedConnectorNamespaces: [],
      supportCaseOpen: false,
    });
    writePersistedChatState(get());
  },

  /** Save current, then load target session. */
  switchSession: (id) => {
    const { saveCurrentSession, activeConversationId } = get();
    saveCurrentSession();
    // A tab can stay open past the retention period: drop what the server has
    // already forgotten before reopening anything (G122).
    const conversations = withinRetention(get().conversations);
    const target = conversations.find((c) => c.id === id);
    if (!target) {
      set({ conversations });
      writePersistedChatState(get());
      return;
    }
    set({
      conversations,
      // A reopened conversation is history, as after a reload: its actions need
      // refreshed guidance (ADR-0018). Re-selecting the open chat keeps it live.
      chat: id === activeConversationId ? target.turns : target.turns.map((turn) => ({ ...turn, restored: true })),
      activeConversationId: id,
      message: '',
      activeTicketId: target.activeTicketId || null,
      officerChat: target.officerChat || null,
      selectedConnectorNamespaces: target.connectorNamespaces ?? [],
      supportCaseOpen: false,
    });
    writePersistedChatState(get());
  },

  /** Delete a session. If it's active, start fresh. */
  deleteSession: (id) => {
    const { conversations, activeConversationId } = get();
    const filtered = conversations.filter((c) => c.id !== id);
    if (id === activeConversationId) {
      set({
        conversations: filtered,
        chat: [GREETING],
        activeConversationId: null,
        message: '',
        activeTicketId: null,
        officerChat: null,
        selectedConnectorNamespaces: [],
      });
    } else {
      set({ conversations: filtered });
    }
    writePersistedChatState(get());
  },

  /**
   * Rename a conversation. Marks the title as user-owned so `saveCurrentSession`
   * stops deriving it from the first message.
   *
   * An empty or whitespace-only title is a no-op rather than an erasure — the
   * inline editor commits on blur, so an accidental click-away must not leave a
   * nameless row.
   */
  renameSession: (id, title) => {
    const next = title.trim().slice(0, 80);
    if (!next) return;
    set((s) => ({
      conversations: s.conversations.map((c) =>
        c.id === id ? { ...c, title: next, titleCustom: true } : c,
      ),
    }));
    writePersistedChatState(get());
  },

  /**
   * Replace the provisional title with the model's summary. A rename wins: a
   * summary that arrives after the user typed a title is dropped.
   */
  setGeneratedTitle: (id, title) => {
    const next = title.trim().slice(0, 80);
    if (!next) return;
    const target = get().conversations.find((c) => c.id === id);
    if (!target || target.titleCustom || (target.titleGenerated && target.title === next)) return;
    set((s) => ({
      conversations: s.conversations.map((c) =>
        c.id === id ? { ...c, title: next, titleGenerated: true } : c,
      ),
    }));
    writePersistedChatState(get());
  },

  /** Pin / unpin. Ordering is applied at read time by `sortConversations`. */
  togglePinSession: (id) => {
    set((s) => ({
      conversations: s.conversations.map((c) =>
        c.id === id ? { ...c, pinned: !c.pinned } : c,
      ),
    }));
    writePersistedChatState(get());
  },

  /**
   * Delete every stored conversation and return to the start screen.
   *
   * This is the settings panel's "clear chat history": local-only, because the
   * conversations live in this browser's localStorage. Server-side history is a
   * separate thing, removed by `DELETE /v1/me` (right to erasure).
   */
  clearAllSessions: () => {
    // The server's copy of an anonymous history is bound to the chat session:
    // a new one means it can no longer be resumed from this browser.
    resetChatSession();
    set({
      conversations: [],
      chat: [GREETING],
      activeConversationId: null,
      message: '',
      activeTicketId: null,
      officerChat: null,
      selectedConnectorNamespaces: [],
    });
    writePersistedChatState(get());
  },

  hydratePersisted: () => {
    const persisted = readPersistedChatState();
    if (!persisted) return;

    const activeConversation = persisted.activeConversationId
      ? persisted.conversations.find((conversation) => conversation.id === persisted.activeConversationId)
      : null;

    set({
      message: persisted.draftMessage || '',
      chat: activeConversation?.turns.length ? activeConversation.turns : [GREETING],
      speechState: 'idle',
      locale: persisted.locale,
      localeExplicit: persisted.localeExplicit === true,
      conversations: persisted.conversations,
      activeConversationId: activeConversation?.id ?? null,
      // A reload mid-case resumes the case and its live chat.
      activeTicketId: activeConversation?.activeTicketId ?? null,
      officerChat: activeConversation?.officerChat ?? null,
      // ...and the services chosen for it.
      selectedConnectorNamespaces: activeConversation?.connectorNamespaces ?? [],
    });
    writePersistedChatState(get());
  },
}));
