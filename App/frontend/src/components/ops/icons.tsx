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
