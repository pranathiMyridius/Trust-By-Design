/*
 * Line icons for the sidebar rail and dashboard (24x24, stroke-based so
 * they pick up `currentColor`). Purely decorative -- every place that
 * uses one also carries a text label or aria-label.
 */

export type NavIconName =
  | "shield"
  | "dashboard"
  | "file-plus"
  | "queue"
  | "files"
  | "reports"
  | "history"
  | "calculator"
  | "approvals"
  | "delegations"
  | "users"
  | "governance"
  | "system"
  | "search"
  | "sparkle"
  | "calendar"
  | "chevron-down"
  | "logout"
  | "upload"
  | "check-circle"
  | "pencil"
  | "x-circle"
  | "bell"
  | "bot"
  | "file"
  | "more";

const PATHS: Record<NavIconName, string[]> = {
  shield: ["M12 3 4.5 6v5.5c0 4.6 3.2 8.4 7.5 9.5 4.3-1.1 7.5-4.9 7.5-9.5V6L12 3Z", "M12 8v4", "M12 15.5h.01"],
  dashboard: ["M12 14 15.5 10.5", "M4 17a8 8 0 1 1 16 0", "M12 14h.01"],
  "file-plus": ["M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z", "M14 3v5h5", "M12 11v6", "M9 14h6"],
  queue: ["M9 6h11", "M9 12h11", "M9 18h11", "m3.5 6 1 1 2-2", "m3.5 12 1 1 2-2", "m3.5 18 1 1 2-2"],
  files: ["M15 3H8a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2V7l-4-4Z", "M15 3v4h4", "M3 8v11a2 2 0 0 0 2 2h9"],
  reports: ["M4 20h16", "M7 16v-5", "M12 16V7", "M17 16v-8"],
  history: ["M3 12a9 9 0 1 0 3-6.7L3 8", "M3 3v5h5", "M12 7v5l3 2"],
  calculator: ["M7 3h10a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2Z", "M8 7h8", "M8 12h.01", "M12 12h.01", "M16 12h.01", "M8 16h.01", "M12 16h.01", "M16 16h.01"],
  approvals: ["M21 12a9 9 0 1 1-9-9", "m9 11 3 3 9-9"],
  delegations: ["M7 7h13", "m16 3 4 4-4 4", "M17 17H4", "m8 13-4 4 4 4"],
  users: ["M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2", "M9 11a4 4 0 1 0 0-8 4 4 0 0 0 0 8Z", "M22 21v-2a4 4 0 0 0-3-3.9", "M16 3.1a4 4 0 0 1 0 7.8"],
  governance: ["M12 3v18", "M5 21h14", "M3 7h18", "m6 7-3 7a3 3 0 0 0 6 0L6 7", "m18 7-3 7a3 3 0 0 0 6 0l-3-7"],
  system: ["M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z", "M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 1 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 1 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 1 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 1 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1Z"],
  search: ["M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14Z", "m20 20-4-4"],
  sparkle: ["M12 3l1.8 5.2L19 10l-5.2 1.8L12 17l-1.8-5.2L5 10l5.2-1.8L12 3Z", "M19 16l.7 2.3L22 19l-2.3.7L19 22l-.7-2.3L16 19l2.3-.7L19 16Z"],
  calendar: ["M7 3v3", "M17 3v3", "M5 6h14a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1Z", "M4 10h16"],
  "chevron-down": ["m6 9 6 6 6-6"],
  logout: ["M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4", "m16 17 5-5-5-5", "M21 12H9"],
  upload: ["M16 16l-4-4-4 4", "M12 12v9", "M20.4 18.4A5 5 0 0 0 18 9h-1.3A8 8 0 1 0 3 16.3"],
  "check-circle": ["M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z", "m8.5 12 2.5 2.5 4.5-5"],
  pencil: ["M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3Z", "m15 5 4 4"],
  "x-circle": ["M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z", "m15 9-6 6", "m9 9 6 6"],
  bell: ["M6 8a6 6 0 1 1 12 0c0 7 3 9 3 9H3s3-2 3-9", "M10.3 21a1.94 1.94 0 0 0 3.4 0"],
  bot: ["M12 8V4H8", "M6 8h12a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2v-8a2 2 0 0 1 2-2Z", "M2 14h2", "M20 14h2", "M9 13v2", "M15 13v2"],
  file: ["M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8l-5-5Z", "M14 3v5h5"],
  more: ["M5 12h.01", "M12 12h.01", "M19 12h.01"],
};

export default function NavIcon({ name, size = 20 }: { name: NavIconName; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.8}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {PATHS[name].map((d) => (
        <path key={d} d={d} />
      ))}
    </svg>
  );
}
