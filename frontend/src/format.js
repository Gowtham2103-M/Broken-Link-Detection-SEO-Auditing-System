export const dateLabel = (value) => {
  if (!value) return "Date unavailable";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString();
};

export const issueCode = (id) => `ISS-${String(id).padStart(3, "0")}`;

export const scoreLabel = (score) =>
  score === null || score === undefined
    ? "—"
    : Number.isInteger(score)
      ? String(score)
      : score.toFixed(1);

export const changeLabel = (change) => {
  if (change === null || change === undefined) return "First scan";
  if (change === 0) return "No change";
  return `${change > 0 ? "+" : "−"}${Math.abs(change).toFixed(1)} vs previous`;
};

export const scoreTone = (score) =>
  score === null || score === undefined
    ? "none"
    : score >= 90
      ? "good"
      : score >= 70
        ? "fair"
        : "poor";

// Severity values returned by the API are Critical / Medium / Low.
export const severityKey = (severity) =>
  severity === "Critical" ? "critical" : severity === "Medium" ? "warning" : "info";

export const TAB_FOR_KEY = { critical: "Critical", warning: "Warning", info: "Info" };
