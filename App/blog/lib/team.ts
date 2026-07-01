// Single source of truth for the real URA Chatbot team: members, their
// individual contribution roles, the efforts shared across all four, and the
// full App project-flow activity ownership. Consumed by components/team-grid.tsx
// (Meet the Team page + landing strip) and the contributor deep-dive headers.

export interface TeamMember {
  /** Display name (user-provided spelling). */
  name: string;
  /** Deep-dive post slug, e.g. 'contributor-mpairwe-lauben'. */
  slug: string;
  /** Public path to the member's photo. */
  photo: string;
  /** Individual contribution areas, shown as role chips. */
  roles: string[];
  /** One-line summary used on cards and the landing strip. */
  blurb: string;
}

export const teamMembers: TeamMember[] = [
  {
    name: 'Mpairwe Lauben',
    slug: 'contributor-mpairwe-lauben',
    photo: '/team/mpairwe-lauben.png',
    roles: ['System Architecture', 'ML Processing', 'Fine-tuning'],
    blurb:
      'Shaped the end-to-end system architecture and owned the machine-learning core — model processing and LoRA fine-tuning for the multilingual RAG engine.',
  },
  {
    name: 'Olowo Omondi Philly',
    slug: 'contributor-olowo-omondi-philly',
    photo: '/team/olowo-omondi-philly.jpeg',
    roles: ['Backend Development', 'TTS Pipeline'],
    blurb:
      'Built the FastAPI backend in python and the services that power it, and engineered the text-to-speech pipeline that gives the chatbot a voice.',
  },
  {
    name: 'Rwemera David',
    slug: 'contributor-rwemera-david',
    photo: '/team/rwemera-david.jpg',
    roles: ['Frontend Design', 'Ingestion Pipeline'],
    blurb:
      'Designed the taxpayer-facing web experience and built the ingestion pipeline that crawls and structures URA knowledge for retrieval.',
  },
  {
    name: 'Okwel Edgar Mark',
    slug: 'contributor-okwel-edgar-mark',
    photo: '/team/okwel-edgar-mark.jpeg',
    roles: ['Optimization', 'STT Pipeline', 'Security Implementation'],
    blurb:
      'Optimised performance across the stack, built the speech-to-text pipeline, and implemented the security guardrails protecting taxpayer data.',
  },
];

/** Tasks every member contributed to jointly. */
export const combinedEfforts: string[] = [
  'SDD Writing',
  'Report Modelling',
  'Database Modelling',
  'Data Collection',
  'Data Design',
];

export interface FlowActivity {
  stage: string;
  activity: string;
  owner: string;
}

/** Full App project-flow, mapping each activity to its owner(s). */
export const projectFlow: FlowActivity[] = [
  { stage: 'System design', activity: 'System Architecture', owner: 'Mpairwe Lauben' },
  { stage: 'ML', activity: 'ML Processing', owner: 'Mpairwe Lauben' },
  { stage: 'ML', activity: 'Model Fine-tuning (LoRA adapters)', owner: 'Mpairwe Lauben' },
  { stage: 'Backend', activity: 'Backend Development (FastAPI / APIs)', owner: 'Olowo Omondi Philly' },
  { stage: 'Voice', activity: 'TTS Pipeline', owner: 'Olowo Omondi Philly' },
  { stage: 'Frontend', activity: 'Frontend Design (Web UI/UX)', owner: 'Rwemera David' },
  { stage: 'Data', activity: 'Ingestion Pipeline (crawl / PDF-CSV ETL)', owner: 'Rwemera David' },
  { stage: 'Performance', activity: 'Optimization', owner: 'Okwel Edgar Mark' },
  { stage: 'Voice', activity: 'STT Pipeline (ASR / Whisper)', owner: 'Okwel Edgar Mark' },
  { stage: 'Security', activity: 'Security Implementation (OWASP LLM, guardrails)', owner: 'Okwel Edgar Mark' },
  { stage: 'Documentation', activity: 'SDD Writing', owner: 'All four members' },
  { stage: 'Documentation', activity: 'Report Modelling', owner: 'All four members' },
  { stage: 'Data', activity: 'Database Modelling', owner: 'All four members' },
  { stage: 'Data', activity: 'Data Collection', owner: 'All four members' },
  { stage: 'Data', activity: 'Data Design', owner: 'All four members' },
];
