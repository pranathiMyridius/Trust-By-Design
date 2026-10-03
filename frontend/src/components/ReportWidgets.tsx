import { useState, type ReactNode } from "react";

import { downloadCsv, formatValue, type Format } from "./reportFormat";
import { RiskLevelIcon } from "./RiskLevelBadge";

/* Stage 17 report building blocks: stat tiles, a single-hue bar list
   for "count by X" breakdowns, and a table that is also the accessible
   / exportable view of every chart. */

export interface Stat {
  label: string;
  value: unknown;
  format?: Format;
  tone?: "danger" | "warning";
  hint?: string;
}

export function StatRow({ stats }: { stats: Stat[] }) {
  return (
    <div className="rp-stats">
      {stats.map((stat) => (
        <div className="rp-stat" key={stat.label} title={stat.hint}>
          <span>{stat.label}</span>
          <strong className={stat.tone && Number(stat.value) > 0 ? `rp-tone-${stat.tone}` : ""}>
            {stat.tone && Number(stat.value) > 0 && (
              <span aria-hidden="true">{stat.tone === "danger" ? "⚠ " : "◔ "}</span>
            )}
            {formatValue(stat.value, stat.format ?? "int")}
          </strong>
          {stat.hint && <small>{stat.hint}</small>}
        </div>
      ))}
    </div>
  );
}

export function Panel({
  title,
  subtitle,
  children,
  wide,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  wide?: boolean;
}) {
  return (
    <section className={`rp-panel ${wide ? "rp-panel-wide" : ""}`}>
      <header>
        <h3>{title}</h3>
        {subtitle && <p>{subtitle}</p>}
      </header>
      {children}
    </section>
  );
}

export interface BarItem {
  key: string;
  label: string;
  value: number;
  detail?: string;
}

/** Horizontal bars, one hue: magnitude only, labelled with the value. */
export function BarList({ items, format = "int", empty = "No data for this period." }: {
  items: BarItem[];
  format?: Format;
  empty?: string;
}) {
  if (!items.length) {
    return <p className="rp-empty">{empty}</p>;
  }
  const max = Math.max(...items.map((item) => item.value), 0) || 1;
  return (
    <ul className="rp-bars" role="list">
      {items.map((item) => (
        <li key={item.key} className="rp-bar-row" title={`${item.label}: ${formatValue(item.value, format)}${item.detail ? ` · ${item.detail}` : ""}`}>
          <span className="rp-bar-label">{item.label}</span>
          <span className="rp-bar-track" aria-hidden="true">
            <span className="rp-bar-fill" style={{ width: `${Math.max(2, (item.value / max) * 100)}%` }} />
          </span>
          <span className="rp-bar-value">{formatValue(item.value, format)}</span>
          {item.detail && <span className="rp-bar-detail">{item.detail}</span>}
        </li>
      ))}
    </ul>
  );
}

export interface Column {
  key: string;
  label: string;
  format?: Format;
  render?: (row: Record<string, unknown>) => ReactNode;
}

const PREVIEW_ROWS = 10;

export function DataTable({
  columns,
  rows,
  filename,
  empty = "Nothing to report for this period.",
}: {
  columns: Column[];
  rows: Record<string, unknown>[];
  filename: string;
  empty?: string;
}) {
  const [showAll, setShowAll] = useState(false);

  if (!rows.length) {
    return <p className="rp-empty">{empty}</p>;
  }
  const visible = showAll ? rows : rows.slice(0, PREVIEW_ROWS);

  return (
    <div className="rp-table-block">
      <div className="rp-table-wrap">
        <table className="rp-table">
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column.key}>{column.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((row, index) => (
              <tr key={`${String(row.assessment_id ?? "")}-${index}`}>
                {columns.map((column) => (
                  <td key={column.key} className={column.format && column.format !== "text" && column.format !== "label" && column.format !== "list" ? "rp-num" : ""}>
                    {column.render ? column.render(row) : formatValue(row[column.key], column.format)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="rp-table-foot">
        <span>
          {rows.length > PREVIEW_ROWS && !showAll ? `Showing ${PREVIEW_ROWS} of ${rows.length}` : `${rows.length} row${rows.length === 1 ? "" : "s"}`}
        </span>
        {rows.length > PREVIEW_ROWS && (
          <button type="button" className="link-button" aria-expanded={showAll} onClick={() => setShowAll((value) => !value)}>
            {showAll ? "Show fewer" : "Show all"}
          </button>
        )}
        <button
          className="link-button"
          onClick={() =>
            downloadCsv(
              filename,
              columns.map((c) => c.label),
              columns.map((c) => c.key),
              rows
            )
          }
        >
          Download CSV
        </button>
      </div>
    </div>
  );
}

// Stage 19: band shown as shape + text (colour is only an extra cue).
export function BandPill({ band }: { band: unknown }) {
  const value = String(band ?? "NOT_ASSESSED");
  const label = String(formatValue(value, "label"));
  return (
    <span className={`rp-band rp-band-${value.toLowerCase()}`} title={`Risk band: ${label}`}>
      <RiskLevelIcon level={value === "NOT_ASSESSED" ? null : value} /> {label}
    </span>
  );
}
