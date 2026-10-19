import { scoreLabel, scoreTone } from "../format";

const RADIUS = 46;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

export function Metrics({ metrics }) {
  const m = metrics || {};
  const cards = [
    ["Pages crawled", m.pages_crawled],
    ["Links checked", m.links_checked],
    ["Broken links", m.broken_links, "critical"],
    ["Redirects", m.redirects],
    ["SEO issues", m.seo_issues],
  ];

  return (
    <section aria-label="Scan metrics">
      <div className="metrics">
        {cards.map(([label, value, tone]) => (
          <div className="metric" key={label}>
            <strong className={tone ? `tone-${tone}` : ""}>{value ?? "—"}</strong>
            <span>{label}</span>
          </div>
        ))}
      </div>
      {metrics && (m.server_errors > 0 || m.access_restricted > 0 || m.unverified_links > 0) && (
        <p className="metric-note">
          Not counted as broken links:
          {m.server_errors > 0 && <span> {m.server_errors} server error{m.server_errors !== 1 ? "s" : ""} (5xx)</span>}
          {m.access_restricted > 0 && <span> · {m.access_restricted} access restricted (401/403/429)</span>}
          {m.unverified_links > 0 && <span> · {m.unverified_links} need verification</span>}
        </p>
      )}
    </section>
  );
}

function Donut({ score }) {
  const value = score ?? 0;
  const tone = scoreTone(score);
  return (
    <svg className="donut" viewBox="0 0 120 120" role="img"
      aria-label={`Health score ${scoreLabel(score)} out of 100`}>
      <circle cx="60" cy="60" r={RADIUS} className="donut-track" />
      <circle cx="60" cy="60" r={RADIUS} className={`donut-value tone-stroke-${tone}`}
        strokeDasharray={`${(CIRCUMFERENCE * value) / 100} ${CIRCUMFERENCE}`}
        transform="rotate(-90 60 60)" />
      <text x="60" y="58" className="donut-number">{scoreLabel(score)}</text>
      <text x="60" y="76" className="donut-caption">out of 100</text>
    </svg>
  );
}

const SEVERITY_ROWS = [
  ["critical", "Critical", 8],
  ["warning", "Warning", 3],
  ["info", "Info", 0],
];

export function HealthSplit({ metrics }) {
  const counts = metrics?.severity_counts || { critical: 0, warning: 0, info: 0 };
  const total = counts.critical + counts.warning + counts.info;
  const pages = metrics?.pages_crawled ?? 0;

  return (
    <section className="split" aria-label="Health and severity">
      <div className="panel health">
        <h2>Health score</h2>
        <Donut score={metrics?.health_score ?? null} />
        <p className="formula">
          100 − {metrics?.penalty_points ?? 0} penalty points
        </p>
      </div>

      <div className="panel severity">
        <h2>Issues by severity</h2>
        {SEVERITY_ROWS.map(([key, label, points]) => (
          <div className="sev-row" key={key}>
            <span className="sev-label"><i className={`dot dot-${key}`} />{label}</span>
            <span className="sev-bar" aria-hidden="true">
              <span className={`bar-${key}`} style={{ width: total ? `${(counts[key] / total) * 100}%` : "0%" }} />
            </span>
            <strong className="sev-count">{counts[key]}</strong>
            <span className="sev-points muted">{points} {points === 1 ? "pt" : "pts"} each</span>
          </div>
        ))}
        <p className="muted small">
          {total} open issue{total !== 1 ? "s" : ""}
          {metrics?.resolved_issues ? ` · ${metrics.resolved_issues} resolved` : ""}
        </p>
      </div>
    </section>
  );
}
