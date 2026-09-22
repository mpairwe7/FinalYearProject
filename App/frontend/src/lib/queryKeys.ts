/**
 * Centralized, type-safe Query Key Factory for TanStack Query v5.
 *
 * Adheres to modern TanStack Query hierarchical key conventions:
 * - Specific keys are sub-arrays of broader category keys.
 * - Allows granular targeting (e.g. invalidating a single ticket) or
 *   broad sweeps (e.g. invalidating all ticket queries on queue mutations).
 */

export const queryKeys = {
  auth: {
    all: () => ['auth'] as const,
    me: (token?: string) => ['auth', 'me', token || 'anonymous'] as const,
    profile: () => ['auth', 'profile'] as const,
    consents: () => ['auth', 'consents'] as const,
    account: () => ['auth', 'account'] as const,
  },
  admin: {
    all: () => ['admin'] as const,
    flags: () => ['admin', 'flags'] as const,
    overrides: () => ['admin', 'overrides'] as const,
    outbox: () => ['admin', 'outbox'] as const,
    evaluation: () => ['admin', 'evaluation'] as const,
  },
  tickets: {
    all: () => ['tickets'] as const,
    queue: (status?: string, limit?: number) =>
      ['tickets', 'queue', { status, limit }] as const,
    queueFull: (status?: string, priority?: string, team?: string, limit?: number, q?: string) =>
      ['tickets', 'queueFull', { status, priority, team, limit, q }] as const,
    detail: (id: string) => ['tickets', 'detail', id] as const,
    stats: (days?: number) => ['tickets', 'stats', days] as const,
    sla: (days?: number) => ['tickets', 'sla', days] as const,
    publicStatus: (id: string) => ['tickets', 'public', id] as const,
  },
  analytics: {
    all: () => ['analytics'] as const,
    dashboard: (days?: number) => ['analytics', 'dashboard', days] as const,
    feedbackSummary: (days?: number) => ['analytics', 'feedbackSummary', days] as const,
  },
  speech: {
    all: () => ['speech'] as const,
    health: () => ['speech', 'health'] as const,
    voices: () => ['speech', 'voices'] as const,
  },
  calls: {
    all: () => ['calls'] as const,
    list: (status?: string) => ['calls', 'list', status] as const,
    detail: (id: string) => ['calls', 'detail', id] as const,
    metrics: (days?: number) => ['calls', 'metrics', days] as const,
  },
} as const;
