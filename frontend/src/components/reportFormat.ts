// Formatting + CSV helpers for the Stage 17 reports (kept out of the
// component files so they stay fast-refresh friendly).

export type Format = "text" | "int" | "num" | "pct" | "days" | "ms" | "usd" | "date" | "datetime" | "bool" | "list" | "label";

export function humanize(value: string): string {
  return value
    .toLowerCase()
    .split("_")
    .map((word) => (word ? word[0].toUpperCase() + word.slice(1) : word))
    .join(" ");
}

export function formatValue(value: unknown, format: Format = "text"): string {
  if (value === null || value === undefined || value === "") {
    return "—";
  }
  switch (format) {
    case "int":
      return Number(value).toLocaleString();
    case "num":
      return Number(value).toLocaleString(undefined, { maximumFractionDigits: 2 });
    case "pct":
      return `${(Number(value) * 100).toLocaleString(undefined, { maximumFractionDigits: 1 })}%`;
    case "days":
      return `${Number(value).toLocaleString(undefined, { maximumFractionDigits: 1 })} d`;
    case "ms":
      return Number(value) >= 1000
        ? `${(Number(value) / 1000).toLocaleString(undefined, { maximumFractionDigits: 1 })} s`
        : `${Math.round(Number(value))} ms`;
    case "usd":
      return `$${Number(value).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 4 })}`;
    case "date":
      return new Date(`${String(value).slice(0, 10)}T00:00:00`).toLocaleDateString(undefined, { dateStyle: "medium" });
    case "datetime": {
      const text = String(value);
      const normalized = /[zZ]|[+-]\d\d:?\d\d$/.test(text) ? text : `${text}Z`;
      return new Date(normalized).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
    }
    case "bool":
      return value ? "Yes" : "No";
    case "list":
      return Array.isArray(value) ? value.join(", ") || "—" : String(value);
    case "label":
      return humanize(String(value));
    default:
      return String(value);
  }
}

function csvCell(value: unknown): string {
  const text = Array.isArray(value) ? value.join("; ") : value === null || value === undefined ? "" : String(value);
  return /[",\n\r]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

export function downloadCsv(filename: string, headers: string[], keys: string[], rows: Record<string, unknown>[]) {
  const lines = [headers.map(csvCell).join(","), ...rows.map((row) => keys.map((key) => csvCell(row[key])).join(","))];
  const blob = new Blob([lines.join("\r\n")], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

export function isoDate(date: Date): string {
  const offset = date.getTimezoneOffset() * 60000;
  return new Date(date.getTime() - offset).toISOString().slice(0, 10);
}
