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

export const CommandIcon = () => (
  <svg {...base}>
    <path d="M15 6a3 3 0 1 1 3 3h-3V6Z" />
    <path d="M9 6a3 3 0 1 0-3 3h3V6Z" />
    <path d="M15 18a3 3 0 1 0 3-3h-3v3Z" />
    <path d="M9 18a3 3 0 1 1-3-3h3v3Z" />
    <rect x="9" y="9" width="6" height="6" rx="1" />
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
