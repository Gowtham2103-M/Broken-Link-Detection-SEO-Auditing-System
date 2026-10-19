import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ExternalLink, LoaderCircle, RefreshCw, Search, X } from "lucide-react";
import { getIssue, recheckIssue } from "../api";
import { dateLabel, issueCode, severityKey } from "../format";

const TABS = [
  ["all", "All"],
  ["critical", "Critical"],
  ["warning", "Warning"],
  ["info", "Info"],
];
const PAGE_SIZE = 10;

function SeverityMark({ issue }) {
  const key = severityKey(issue.severity);
  return (
    <span className={`sev-mark sev-${key}`}>
      <i className={`dot dot-${key}`} />
      {issue.severity_label}
    </span>
  );
}

function Detail({ state, issue, onRecheck, rechecking }) {
  if (state.loading) {
    return <div className="detail-loading"><LoaderCircle size={14} className="spin" /> Loading explanation…</div>;
  }
  if (state.error) {
    return <div className="inline-error" role="alert">{state.error}</div>;
  }

  const { data } = state;
  const agent = data.agent;

  return (
    <div className="detail">
      <div className="detail-head">
        <div>
          <span className="muted small">{issueCode(issue.issue_id)} · {data.category_label}</span>
          <h3>{issue.issue_type}</h3>
        </div>
        <button className="btn primary" onClick={onRecheck} disabled={rechecking}>
          {rechecking ? <LoaderCircle size={14} className="spin" /> : <RefreshCw size={14} />}
          {rechecking ? "Rechecking…" : "Recheck"}
        </button>
      </div>

      <dl className="facts">
        <div><dt>Target URL</dt><dd>
          <a href={data.target_url} target="_blank" rel="noreferrer">{data.target_url}<ExternalLink size={11} /></a>
        </dd></div>
        <div><dt>Found on</dt><dd>{data.source_url || "—"}</dd></div>
        <div><dt>HTTP status</dt><dd>{data.http_status ?? "No response"}</dd></div>
        <div><dt>State</dt><dd>{data.status}</dd></div>
      </dl>

      <div className="agent" aria-label="Agent explanation">
        <h4>Agent explanation</h4>
        <p>{agent.explanation}</p>

        <h5>Cause</h5>
        <p>{agent.cause}</p>

        <h5>Why it matters</h5>
        <p>{agent.impact}</p>

        <h5>Recommended fix</h5>
        <p>{agent.recommended_action}</p>

        {agent.example_fix && <pre>{agent.example_fix}</pre>}
        {agent.crawler_detail && <p className="muted small">Crawler note: {agent.crawler_detail}</p>}
      </div>

      {data.rechecks?.length > 0 && (
        <div className="rechecks">
          <h5>Recheck history</h5>
          <ul>
            {data.rechecks.slice(0, 3).map((item) => (
              <li key={item.recheck_id}>
                <span className={`state state-${String(item.issue_status).toLowerCase()}`}>{item.issue_status}</span>
                <span>{dateLabel(item.checked_at)}</span>
                <span className="muted">
                  {item.http_status ? `HTTP ${item.http_status}` : "No response"}
                  {item.outcome ? ` · ${item.outcome}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function IssueTable({ scanId, issues, loading, onChanged, notify, fail }) {
  const [tab, setTab] = useState("all");
  const [stateFilter, setStateFilter] = useState("Open");
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [openId, setOpenId] = useState(null);
  const [detail, setDetail] = useState({ loading: false, error: "", data: null });
  const [recheckingId, setRecheckingId] = useState(null);
  const detailRef = useRef(null);
  const requestRef = useRef(0);

  // A different scan is a different table: reset everything view-related.
  useEffect(() => {
    setTab("all"); setSearch(""); setPage(1); setOpenId(null);
  }, [scanId]);

  const byState = useMemo(
    () => issues.filter((issue) => stateFilter === "All" || issue.status === stateFilter),
    [issues, stateFilter]
  );

  const counts = useMemo(() => {
    const result = { all: byState.length, critical: 0, warning: 0, info: 0 };
    byState.forEach((issue) => { result[severityKey(issue.severity)] += 1; });
    return result;
  }, [byState]);

  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    return byState.filter((issue) => {
      if (tab !== "all" && severityKey(issue.severity) !== tab) return false;
      if (!needle) return true;
      return [
        issueCode(issue.issue_id), issue.issue_type, issue.category_label,
        issue.source_url, issue.target_url, issue.http_status, issue.detail,
      ].join(" ").toLowerCase().includes(needle);
    });
  }, [byState, tab, search]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, pageCount);
  const visible = filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE);
  const from = filtered.length ? (currentPage - 1) * PAGE_SIZE + 1 : 0;
  const to = Math.min(currentPage * PAGE_SIZE, filtered.length);

  async function loadDetail(issueId) {
    const ticket = ++requestRef.current;
    setDetail({ loading: true, error: "", data: null });
    try {
      const data = await getIssue(issueId);
      if (ticket === requestRef.current) setDetail({ loading: false, error: "", data });
    } catch (error) {
      if (ticket === requestRef.current) {
        setDetail({ loading: false, error: error.message || "Could not load this issue.", data: null });
      }
    }
  }

  function toggle(issueId) {
    if (openId === issueId) {
      setOpenId(null);
      return;
    }
    setOpenId(issueId);
    loadDetail(issueId);
  }

  // Bring the expanded detail into view so Open always visibly does something.
  useEffect(() => {
    if (openId !== null && detailRef.current?.scrollIntoView) {
      detailRef.current.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, [openId, detail.loading]);

  async function handleRecheck(issue) {
    setRecheckingId(issue.issue_id);
    try {
      const result = await recheckIssue(issue.issue_id);
      const resolved = result.status === "Resolved";
      notify(`${issueCode(issue.issue_id)} ${resolved ? "resolved" : "is still open"}: ${result.outcome || "recheck complete."}`);
      await onChanged();
      if (resolved && stateFilter === "Open") {
        setOpenId(null);
      } else {
        await loadDetail(issue.issue_id);
      }
    } catch (error) {
      fail(error.message || "Could not recheck this issue.");
    } finally {
      setRecheckingId(null);
    }
  }

  return (
    <section className="panel issues" aria-label="Issues">
      <div className="panel-head">
        <h2>Issues</h2>
        <label className="state-filter">
          <span className="muted small">Show</span>
          <select value={stateFilter} onChange={(event) => { setStateFilter(event.target.value); setPage(1); }}>
            <option>Open</option>
            <option>Resolved</option>
            <option>All</option>
          </select>
        </label>
      </div>

      <div className="issue-tools">
        <div className="tabs" role="tablist" aria-label="Filter by severity">
          {TABS.map(([key, label]) => (
            <button key={key} role="tab" aria-selected={tab === key}
              className={`tab ${tab === key ? "active" : ""}`}
              onClick={() => { setTab(key); setPage(1); }}>
              {key !== "all" && <i className={`dot dot-${key}`} />}
              {label}<span className="count">{counts[key]}</span>
            </button>
          ))}
        </div>
        <div className="search">
          <Search size={14} />
          <input value={search} placeholder="Search URL, problem or status…"
            aria-label="Search issues"
            onChange={(event) => { setSearch(event.target.value); setPage(1); }} />
          {search && (
            <button className="icon-btn" aria-label="Clear search" onClick={() => setSearch("")}><X size={13} /></button>
          )}
        </div>
      </div>

      <div className="table-wrap">
        <table className="data-table issues-table">
          <thead>
            <tr>
              <th>Severity</th>
              <th>Detected problem</th>
              <th>HTTP</th>
              <th>Source URL</th>
              <th>State</th>
              <th><span className="sr-only">Actions</span></th>
            </tr>
          </thead>
          <tbody>
            {visible.map((issue) => {
              const isOpen = openId === issue.issue_id;
              return (
                <Fragment key={issue.issue_id}>
                  <tr className={isOpen ? "row-open" : ""}>
                    <td data-label="Severity"><SeverityMark issue={issue} /></td>
                    <td data-label="Problem" className="problem-cell">
                      <strong>{issue.issue_type}</strong>
                      <span className="url" title={issue.target_url}>{issue.target_url}</span>
                    </td>
                    <td data-label="HTTP">
                      <span className={`http ${issue.http_status >= 400 ? "bad" : ""}`}>{issue.http_status ?? "—"}</span>
                    </td>
                    <td data-label="Source" className="url source-cell" title={issue.source_url}>{issue.source_url}</td>
                    <td data-label="State">
                      <span className={`state state-${issue.status.toLowerCase()}`}>{issue.status}</span>
                    </td>
                    <td className="actions-cell">
                      <button className="btn" aria-expanded={isOpen} aria-controls={`issue-detail-${issue.issue_id}`}
                        onClick={() => toggle(issue.issue_id)}>
                        {isOpen ? "Close" : "Open"}
                        <ChevronDown size={13} className={isOpen ? "flip" : ""} />
                      </button>
                    </td>
                  </tr>
                  {isOpen && (
                    <tr className="detail-row" id={`issue-detail-${issue.issue_id}`} ref={detailRef}>
                      <td colSpan={6}>
                        <Detail state={detail} issue={issue}
                          rechecking={recheckingId === issue.issue_id}
                          onRecheck={() => handleRecheck(issue)} />
                      </td>
                    </tr>
                  )}
                </Fragment>
              );
            })}

            {loading && !visible.length && (
              <tr><td colSpan={6} className="empty">Loading issues…</td></tr>
            )}
            {!loading && !visible.length && (
              <tr><td colSpan={6} className="empty">
                {issues.length === 0
                  ? "No issues were recorded for this scan."
                  : "No issues match the current filters."}
              </td></tr>
            )}
          </tbody>
        </table>
      </div>

      <div className="table-foot">
        <span className="muted small">
          {filtered.length ? `Showing ${from}–${to} of ${filtered.length}` : "Nothing to show"}
        </span>
        <div className="pager">
          <button className="btn" disabled={currentPage <= 1} onClick={() => setPage(currentPage - 1)}>Previous</button>
          <span className="small">Page {currentPage} of {pageCount}</span>
          <button className="btn" disabled={currentPage >= pageCount} onClick={() => setPage(currentPage + 1)}>Next</button>
        </div>
      </div>
    </section>
  );
}
