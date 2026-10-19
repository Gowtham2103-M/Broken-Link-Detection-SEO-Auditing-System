import { useState } from "react";
import { RefreshCw, Search, X } from "lucide-react";
import { changeLabel, dateLabel, scoreLabel, scoreTone } from "../format";

function ScanCard({ scan, selected, busy, onView, onRescan }) {
  const metrics = scan.metrics;
  const counts = metrics.severity_counts;
  const finished = scan.status === "completed";
  const change = scan.score_change;

  return (
    <article className={`panel history-card ${selected ? "selected" : ""}`}>
      <header className="history-top">
        <div className="history-site">
          <h3>{scan.domain}</h3>
          <a className="url" href={scan.start_url} target="_blank" rel="noreferrer">{scan.start_url}</a>
          <span className="muted small">
            Scan #{scan.scan_id} · {dateLabel(scan.scan_time)}
          </span>
        </div>

        {finished ? (
          <div className="history-score">
            <strong className={`tone-${scoreTone(scan.health_score)}`}>{scoreLabel(scan.health_score)}</strong>
            <span className="muted small">health score</span>
            <span className={`delta ${change > 0 ? "up" : change < 0 ? "down" : ""}`}>{changeLabel(change)}</span>
          </div>
        ) : (
          <div className="history-score">
            <span className={`state state-${scan.status}`}>{scan.status === "running" ? "Running" : "Failed"}</span>
          </div>
        )}
      </header>

      {finished ? (
        <dl className="history-stats">
          <div><dt>Pages crawled</dt><dd>{scan.pages_inspected}</dd></div>
          <div><dt><i className="dot dot-critical" />Broken / critical</dt><dd>{counts.critical}</dd></div>
          <div><dt><i className="dot dot-warning" />Warnings</dt><dd>{counts.warning}</dd></div>
          <div><dt><i className="dot dot-info" />Info</dt><dd>{counts.info}</dd></div>
        </dl>
      ) : (
        <p className="muted small history-note">
          {scan.status === "running"
            ? `In progress: ${scan.pages_inspected} pages crawled so far.`
            : scan.notes || "This scan did not complete."}
        </p>
      )}

      <footer className="history-actions">
        {scan.notes && finished && <span className="muted small note">{scan.notes}</span>}
        <button className="btn" onClick={() => onView(scan.scan_id)} disabled={!finished}>View report</button>
        <button className="btn primary" onClick={() => onRescan(scan)} disabled={busy || scan.status === "running"}>
          <RefreshCw size={13} /> Re-scan Now
        </button>
      </footer>
    </article>
  );
}

export default function History({ scans, selectedScanId, loading, busy, onView, onRescan, onRefresh }) {
  const [search, setSearch] = useState("");
  const needle = search.trim().toLowerCase();
  const shown = scans.filter((scan) => `${scan.domain} ${scan.start_url}`.toLowerCase().includes(needle));

  return (
    <section aria-label="Scan history">
      <div className="panel-head page-head">
        <div>
          <h1>Scan history</h1>
          <p className="muted">Past audits for every site. View a saved report or re-scan the same site.</p>
        </div>
        <button className="btn" onClick={onRefresh}><RefreshCw size={13} /> Refresh</button>
      </div>

      <div className="search history-search">
        <Search size={14} />
        <input value={search} aria-label="Search history by domain"
          placeholder="Search by domain…" onChange={(event) => setSearch(event.target.value)} />
        {search && <button className="icon-btn" aria-label="Clear search" onClick={() => setSearch("")}><X size={13} /></button>}
      </div>

      <div className="history-grid">
        {shown.map((scan) => (
          <ScanCard key={scan.scan_id} scan={scan} busy={busy}
            selected={Number(scan.scan_id) === Number(selectedScanId)}
            onView={onView} onRescan={onRescan} />
        ))}
      </div>

      {!shown.length && (
        <p className="empty-block">
          {loading ? "Loading scan history…" : scans.length ? "No scans match that domain." : "No scans yet. Run your first scan from the Scan page."}
        </p>
      )}
    </section>
  );
}
