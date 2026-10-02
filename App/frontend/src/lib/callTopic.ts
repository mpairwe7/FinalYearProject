/**
 * Readable names for the topics a transfer is filed under. The backend's
 * handoff packet uses snake_case keys (`objection_or_dispute`); anything this
 * table does not know is humanised rather than shown raw.
 */
const TOPIC_LABEL: Record<string, string> = {
  general_tax_support: 'General tax support',
  objection_or_dispute: 'Objection or dispute',
  account_specific: 'Account question',
  customs: 'Customs',
  registration: 'Registration',
};

export function callTopicLabel(topic?: string | null): string {
  const key = (topic || '').trim();
  if (!key) return '';
  return TOPIC_LABEL[key] ?? key.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase());
}

/** Priorities worth a chip: an officer should see these before opening the call. */
export function isRaisedPriority(priority?: string | null): priority is 'high' | 'urgent' {
  return priority === 'high' || priority === 'urgent';
}
