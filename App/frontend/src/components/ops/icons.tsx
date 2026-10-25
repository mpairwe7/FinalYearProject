import React from "react";

/**
 * Icons the operations console needs and the chat surface does not.
 *
 * Same drawing conventions as components/Icons.tsx — 24-unit viewBox,
 * `currentColor`, 1.6 stroke — so the two sets sit together without a seam.
 * Sized by CSS (`.ops-icon-btn svg`) rather than fixed attributes, because the
 * console uses the same glyph at 14px in a chip and 17px in a toolbar button.
 */

const base = {
  "aria-hidden": true as const,
  viewBox: "0 0 24 24",
  fill: "none",
  stroke: "currentColor",
  strokeWidth: 1.6,
  strokeLinecap: "round" as const,
  strokeLinejoin: "round" as const,
  width: 16,
  height: 16,
};

export const RefreshIcon = () => (
  <svg {...base}>
    <path d="M20 11a8 8 0 0 0-13.7-5.3L4 8" />
    <path d="M4 4v4h4" />
    <path d="M4 13a8 8 0 0 0 13.7 5.3L20 16" />
    <path d="M20 20v-4h-4" />
  </svg>
);

export const InboxIcon = () => (
  <svg {...base}>
    <path d="M4 13h4l1.5 3h5L16 13h4" />
    <path d="M5.5 5h13l1.5 8v4a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-4l1.5-8Z" />
  </svg>
);

export const FlagIcon = () => (
  <svg {...base}>
    <path d="M5 21V4" />
    <path d="M5 5h11l-1.6 3.5L16 12H5" />
  </svg>
);

export const ChartIcon = () => (
  <svg {...base}>
    <path d="M4 20V6" /><path d="M4 20h16" />
    <path d="M8 20v-6" /><path d="M13 20V9" /><path d="M18 20v-9" />
  </svg>
);

export const GaugeIcon = () => (
  <svg {...base}>
    <path d="M4 18a8 8 0 1 1 16 0" />
    <path d="m14.5 10.5-2.9 4" />
    <circle cx="12" cy="18" r="1.2" />
  </svg>
);

export const AlertTriangleIcon = () => (
  <svg {...base}>
    <path d="M10.3 4.3 2.7 17.2A1.6 1.6 0 0 0 4.1 19.6h15.8a1.6 1.6 0 0 0 1.4-2.4L13.7 4.3a1.6 1.6 0 0 0-2.8 0Z" />
    <path d="M12 9.5v4" /><path d="M12 16.6h.01" />
  </svg>
);

export const ClockIcon = () => (
  <svg {...base}>
    <circle cx="12" cy="12" r="8" />
    <path d="M12 7.5V12l3 1.8" />
  </svg>
);

export const ArrowUpRightIcon = () => (
  <svg {...base}>
    <path d="M8 16 16 8" /><path d="M9.5 8H16v6.5" />
  </svg>
);

export const InboxEmptyIcon = () => (
  <svg {...base} width={20} height={20}>
    <rect x="3.5" y="5" width="17" height="14" rx="2.5" />
    <path d="M3.5 12.5h4l1.4 2.4h6.2l1.4-2.4h4" />
  </svg>
);

/* --------------------------------------------------------------------------
 * Sidebar rail glyphs
 *
 * The console nav became a collapsed 52px rail where the icon is the ONLY
 * label, so every staff destination needs one — a text-only row is invisible
 * there. Same conventions as above.
 * ------------------------------------------------------------------------ */

export const ListIcon = () => (
  <svg {...base}>
    <path d="M8 6h12" /><path d="M8 12h12" /><path d="M8 18h12" />
    <path d="M4 6h.01" /><path d="M4 12h.01" /><path d="M4 18h.01" />
  </svg>
);

export const SlidersIcon = () => (
  <svg {...base}>
    <path d="M4 6h9" /><path d="M17 6h3" />
    <path d="M4 12h3" /><path d="M11 12h9" />
    <path d="M4 18h9" /><path d="M17 18h3" />
    <circle cx="15" cy="6" r="2" /><circle cx="9" cy="12" r="2" /><circle cx="15" cy="18" r="2" />
  </svg>
);

export const SendIcon = () => (
  <svg {...base}>
    <path d="M20 4 3 10.5l6.5 2.6L12 20l8-16Z" />
    <path d="M9.5 13.1 20 4" />
  </svg>
);

export const BeakerIcon = () => (
  <svg {...base}>
    <path d="M9.5 3v6.2L4.6 17a2 2 0 0 0 1.7 3h11.4a2 2 0 0 0 1.7-3l-4.9-7.8V3" />
    <path d="M8 3h8" /><path d="M7 14h10" />
  </svg>
);

export const PanelLeftIcon = () => (
  <svg {...base}>
    <rect x="3" y="4" width="18" height="16" rx="2" />
    <path d="M9 4v16" />
  </svg>
);

/* --------------------------------------------------------------------------
 * Sidebar chrome
 *
 * The rail's own controls, as opposed to its destinations: the search field's
 * glyph, the collapse/expand arrows, and the account menu's help item.
 * ------------------------------------------------------------------------ */

/** The search field's leading glyph. Was the ⌘ command loop, which read as a
 *  decoration rather than "type here to find something". */
export const SearchIcon = () => (
  <svg {...base}>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3.7-3.7" />
  </svg>
);

/* Double chevrons rather than a pin: the control widens and narrows the rail,
   and an arrow pointing the way it will move says that without a metaphor. */
export const ChevronsLeftIcon = () => (
  <svg {...base}>
    <path d="m12.5 17-5-5 5-5" /><path d="m18 17-5-5 5-5" />
  </svg>
);

export const ChevronsRightIcon = () => (
  <svg {...base}>
    <path d="m11.5 7 5 5-5 5" /><path d="m6 7 5 5-5 5" />
  </svg>
);

export const ChevronDownIcon = () => (
  <svg {...base}>
    <path d="m6 9.5 6 6 6-6" />
  </svg>
);

export const HelpCircleIcon = () => (
  <svg {...base}>
    <circle cx="12" cy="12" r="9" />
    <path d="M9.6 9.4a2.5 2.5 0 0 1 4.9.6c0 1.7-2.5 2-2.5 3.6" />
    <path d="M12 17.3h.01" />
  </svg>
);

export const CheckIcon = () => (
  <svg {...base}>
    <path d="m5 12.5 4.5 4.5L19 7.5" />
  </svg>
);

export const PhoneIcon = () => (
  <svg {...base}>
    <path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72 12.84 12.84 0 0 0 .7 2.81 2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45 12.84 12.84 0 0 0 2.81.7A2 2 0 0 1 22 16.92z" />
  </svg>
);

/** A clipboard with a tick: the service-quality audit. */
export const ClipboardCheckIcon = () => (
  <svg {...base}>
    <path d="M9 4h6v3H9z" />
    <path d="M15 5h2a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V7a2 2 0 0 1 2-2h2" />
    <path d="m9 14 2 2 4-4" />
  </svg>
);

/** A shield: the tamper-evident audit trail. */
export const ShieldIcon = () => (
  <svg {...base}>
    <path d="M12 3 5 6v5c0 4.4 3 8.3 7 10 4-1.7 7-5.6 7-10V6l-7-3Z" />
  </svg>
);

export const BugIcon = () => (
  <svg {...base}>
    <rect x="8" y="6" width="8" height="14" rx="4" />
    <path d="m19 7-3 2" /><path d="m5 7 3 2" />
    <path d="m19 19-3-2" /><path d="m5 19 3-2" />
    <path d="M20 13h-4" /><path d="M4 13h4" />
    <path d="m10 4 1 2" /><path d="m14 4-1 2" />
  </svg>
);

export const PlugIcon = () => (
  <svg {...base}>
    <path d="M12 22v-5" />
    <path d="M9 8V2" /><path d="M15 8V2" />
    <path d="M18 8v5a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4V8Z" />
  </svg>
);

export const GlobeIcon = () => (
  <svg {...base} width={14} height={14}>
    <circle cx="12" cy="12" r="9" />
    <path d="M3 12h18" />
    <path d="M12 3a13.5 13.5 0 0 1 0 18 13.5 13.5 0 0 1 0-18Z" />
  </svg>
);

export const BuildingIcon = () => (
  <svg {...base} width={13} height={13}>
    <rect x="4" y="3" width="16" height="18" rx="1.5" />
    <path d="M9 7h2" /><path d="M13 7h2" />
    <path d="M9 11h2" /><path d="M13 11h2" />
    <path d="M9 15h2" /><path d="M13 15h2" />
    <path d="M10 21v-3h4v3" />
  </svg>
);

export const UserIcon = () => (
  <svg {...base} width={13} height={13}>
    <circle cx="12" cy="8" r="4" />
    <path d="M5 20a7 7 0 0 1 14 0" />
  </svg>
);

export const CopyIcon = () => (
  <svg {...base} width={14} height={14}>
    <rect x="9" y="9" width="11" height="11" rx="2" />
    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
  </svg>
);

export const LinkIcon = () => (
  <svg {...base} width={14} height={14}>
    <path d="M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71" />
    <path d="M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71" />
  </svg>
);

export const MicIcon = () => (
  <svg {...base} width={13} height={13}>
    <rect x="9" y="3" width="6" height="11" rx="3" />
    <path d="M5 11a7 7 0 0 0 14 0" />
    <path d="M12 18v3" />
  </svg>
);

export const MailIcon = () => (
  <svg {...base} width={14} height={14}>
    <rect x="3" y="5" width="18" height="14" rx="2" />
    <path d="m3 7 9 6 9-6" />
  </svg>
);

export const SmartphoneIcon = () => (
  <svg {...base} width={14} height={14}>
    <rect x="6" y="2" width="12" height="20" rx="2" />
    <path d="M12 18h.01" />
  </svg>
);

export const BellIcon = () => (
  <svg {...base} width={14} height={14}>
    <path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9" />
    <path d="M10.3 21a1.94 1.94 0 0 0 3.4 0" />
  </svg>
);

export const ZapIcon = () => (
  <svg {...base} width={14} height={14}>
    <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
  </svg>
);

export const SparklesIcon = () => (
  <svg {...base} width={12} height={12}>
    <path d="m12 3-1.9 5.8a2 2 0 0 1-1.3 1.3L3 12l5.8 1.9a2 2 0 0 1 1.3 1.3L12 21l1.9-5.8a2 2 0 0 1 1.3-1.3L21 12l-5.8-1.9a2 2 0 0 1-1.3-1.3L12 3Z" />
  </svg>
);

export const CloseIcon = () => (
  <svg {...base} width={14} height={14}>
    <path d="M18 6 6 18" /><path d="m6 6 12 12" />
  </svg>
);

export const DownloadIcon = () => (
  <svg {...base} width={14} height={14}>
    <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" />
    <polyline points="7 10 12 15 17 10" />
    <line x1="12" y1="15" x2="12" y2="3" />
  </svg>
);

export const InfoCircleIcon = () => (
  <svg {...base}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 16v-4" /><path d="M12 8h.01" />
  </svg>
);

