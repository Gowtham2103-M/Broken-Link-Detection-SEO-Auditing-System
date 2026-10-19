import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowDownToLine, CircleCheck, CircleAlert, X } from "lucide-react";
import {
  exportUrl,
  getHealth,
  getIssues,
  getPages,
  getScanProgress,
  getScans,
  startScan,
} from "./api";
import { dateLabel } from "./format";
import ScanForm from "./components/ScanForm";
import { HealthSplit, Metrics } from "./components/Dashboard";
import IssueTable from "./components/IssueTable";
import PagesTable from "./components/PagesTable";
import History from "./components/History";

const POLL_MS = 1000;

const CONNECTION_FAILURE_PATTERN =
  /could not reach|connection refused|failed to establish a new connection|name resolution|getaddrinfo|connectionerror|httprequest|max retries exceeded|actively refused|timed out|timeout|failed to resolve|networkerror|failed to fetch|connection pool/i;

function isConnectionFailure(message = "") {
  return CONNECTION_FAILURE_PATTERN.test(String(message));
}

function friendlyConnectionMessage() {
  return "Website could not be reached. Check that the URL is correct and that the website is running, then try again.";
}

export default function App() {
  const [view, setView] = useState("Scan");
  const [apiStatus, setApiStatus] = useState("Connecting…");
  const [scans, setScans] = useState([]);
  const [selectedScanId, setSelectedScanId] = useState(null);
  const [issues, setIssues] = useState([]);
  const [pages, setPages] = useState([]);
  const [loading, setLoading] = useState(true);
  const [runningScanId, setRunningScanId] = useState(null);
  const [progress, setProgress] = useState(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const selectedRef = useRef(null);
  const [warning, setWarning] = useState("");

  selectedRef.current = selectedScanId;
  const selectedScan = scans.find((scan) => scan.scan_id === selectedScanId) || null;
  const scanning = runningScanId !== null;

  // Loads a scan's issues and pages. Metrics come from the scans list so the
  // cards, donut and history all use the same backend numbers.
  const loadScanData = useCallback(async (scanId) => {
    if (scanId == null) {
      setIssues([]);
      setPages([]);
      return;
    }

    const [issueData, pageData] = await Promise.all([
      getIssues(scanId),
      getPages(scanId),
    ]);

    if (selectedRef.current === scanId) {
      setIssues(issueData);
      setPages(pageData);
    }
  }, []);

  const refresh = useCallback(async (preferredId) => {
    const list = await getScans();
    setScans(list);

    const stillThere = (id) => list.some((scan) => scan.scan_id === id);
    const finished = list.filter((scan) => scan.status === "completed");

    let next = preferredId ?? selectedRef.current;
    if (!stillThere(next)) {
      next = finished[0]?.scan_id ?? list[0]?.scan_id ?? null;
    }

    selectedRef.current = next;
    setSelectedScanId(next);
    await loadScanData(next);

    // Resume progress display if a scan is still running (e.g. after a reload).
    const running = list.find((scan) => scan.status === "running");
    setRunningScanId((current) => current ?? running?.scan_id ?? null);
    return list;
  }, [loadScanData]);

  useEffect(() => {
    let cancelled = false;

    (async () => {
      try {
        await getHealth();
        if (cancelled) return;

        setApiStatus("Connected");
        await refresh();
      } catch (err) {
        console.error("Initial SiteAudit connection error:", err);
        if (!cancelled) {
          setApiStatus("Not connected");
          setError("SiteAudit couldn't connect to its backend. Make sure the backend server is running, then refresh the page.");
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [refresh]);

  // Poll the real progress of the running scan.
  useEffect(() => {
    if (runningScanId === null) return undefined;

    let stopped = false;
    let timer;

    const tick = async () => {
      try {
        const status = await getScanProgress(runningScanId);
        if (stopped) return;

        setProgress(status);

        if (status.status === "running") {
          timer = setTimeout(tick, POLL_MS);
          return;
        }

        const finishedId = runningScanId;
        setRunningScanId(null);
        setProgress(null);

        if (status.status === "completed") {
          setNotice(
            `Scan #${finishedId} completed: ${status.pages_crawled} pages crawled, ${status.issues_detected} issues found.${status.notes ? ` ${status.notes}` : ""}`
          );
          await refresh(finishedId);
        } else {
          const details = status.notes || "";
          if (isConnectionFailure(details)) {
            setWarning(friendlyConnectionMessage());
          } else {
            setError("The scan couldn't be completed. Please check the website URL and try again.");
          }
          console.error("Scan failed:", details || "No failure details provided.");
          await refresh();
        }
      } catch (err) {
        if (stopped) return;

        console.error("Scan progress polling error:", err);
        if (isConnectionFailure(err?.message)) {
          setWarning("Connection interrupted. SiteAudit couldn't retrieve the scan status. Check that the backend is running, then try again.");
        } else {
          setError("SiteAudit temporarily lost contact with the backend while checking scan progress. Please try again.");
        }

        setRunningScanId(null);
        setProgress(null);
      }
    };

    tick();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [runningScanId, refresh]);

  async function launchScan({ url, pageLimit, crawlDepth }) {
    setError("");
    setNotice("");
    setWarning("");

    try {
      const result = await startScan(url, pageLimit, crawlDepth);
      setSelectedScanId(result.scan_id);
      selectedRef.current = result.scan_id;

      if (result.already_running) {
        setNotice("A scan of this website is already running; showing its progress.");
      }

      setProgress(null);
      setRunningScanId(result.scan_id);
      setView("Scan");
    } catch (err) {
      const message = err?.message || "";
      console.error("Could not start scan:", err);

      if (isConnectionFailure(message)) {
        setWarning(friendlyConnectionMessage());
      } else {
        setError("We couldn't start the scan. Check the URL and make sure the backend is available, then try again.");
      }
    }
  }

  async function rescan(scan) {
    await launchScan({
      url: scan.start_url,
      pageLimit: scan.page_limit || 50,
      crawlDepth: scan.crawl_depth ?? null,
    });
  }

  async function viewReport(scanId) {
    setError("");
    setWarning("");
    setNotice("");
    selectedRef.current = scanId;
    setSelectedScanId(scanId);
    setLoading(true);

    try {
      await loadScanData(scanId);
      setView("Scan");
    } catch (err) {
      console.error("Could not load scan report:", err);
      setError("We couldn't load this report. Check that the backend is running and try again.");
    } finally {
      setLoading(false);
    }
  }

  const reloadAfterChange = useCallback(() => refresh(selectedRef.current), [refresh]);

  const metrics = selectedScan?.metrics ?? null;
  const hasReport = selectedScan && selectedScan.status === "completed";

  function dismissMessages() {
    setError("");
    setWarning("");
    setNotice("");
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="topbar-inner">
          <span className="brand">SiteAudit</span>
          <nav aria-label="Main">
            {["Scan", "History"].map((name) => (
              <button
                key={name}
                className={`nav-link ${view === name ? "active" : ""}`}
                aria-current={view === name ? "page" : undefined}
                onClick={() => {
                  setView(name);
                  dismissMessages();
                }}
              >
                {name}
              </button>
            ))}
          </nav>
          <span className="conn" role="status">
            <i className={`dot ${apiStatus === "Connected" ? "dot-ok" : "dot-bad"}`} />
            {apiStatus}
          </span>
        </div>
      </header>

      <main className="content">
        {error && (
          <div className="banner banner-error" role="alert">
            <CircleAlert size={15} />
            <span>{error}</span>
            <button className="icon-btn" aria-label="Dismiss error" onClick={() => setError("")}>
              <X size={13} />
            </button>
          </div>
        )}

        {warning && (
          <div className="banner banner-warn" role="status">
            <CircleAlert size={15} />
            <span>{warning}</span>
            <button className="icon-btn" aria-label="Dismiss warning" onClick={() => setWarning("")}>
              <X size={13} />
            </button>
          </div>
        )}

        {notice && (
          <div className="banner banner-ok" role="status">
            <CircleCheck size={15} />
            <span>{notice}</span>
            <button className="icon-btn" aria-label="Dismiss notice" onClick={() => setNotice("")}>
              <X size={13} />
            </button>
          </div>
        )}

        <div hidden={view !== "History"}>
          <History
            scans={scans}
            selectedScanId={selectedScanId}
            loading={loading}
            busy={scanning}
            onView={viewReport}
            onRescan={rescan}
            onRefresh={() =>
              refresh().catch((err) => {
                console.error("Could not refresh scan history:", err);
                setError("Couldn't refresh scan history. Check that the backend is running and try again.");
              })
            }
          />
        </div>

        <div className="scan-view" hidden={view !== "Scan"}>
          <div className="page-head">
            <h1>Scan a website</h1>
            <p className="muted">Crawl internal links, check every link's status, and review SEO findings.</p>
          </div>

          <ScanForm
            onSubmit={launchScan}
            scanning={scanning}
            progress={progress}
            defaults={selectedScan ? {
              url: selectedScan.start_url,
              crawlDepth: selectedScan.crawl_depth,
              pageLimit: selectedScan.page_limit,
            } : null}
          />

          {hasReport ? (
            <>
              <p className="report-line muted small">
                Report for <strong>{selectedScan.domain}</strong> · scan #{selectedScan.scan_id} · {dateLabel(selectedScan.scan_time)}
                {selectedScan.crawl_depth !== null && selectedScan.crawl_depth !== undefined
                  ? ` · depth ${selectedScan.crawl_depth}`
                  : " · unlimited depth"}
                {" · limit "}{selectedScan.page_limit} pages
              </p>
              {selectedScan.notes && <p className="banner banner-warn">{selectedScan.notes}</p>}

              <Metrics metrics={metrics} />
              <HealthSplit metrics={metrics} />

              <IssueTable
                scanId={selectedScan.scan_id}
                issues={issues}
                loading={loading}
                onChanged={reloadAfterChange}
                notify={setNotice}
                fail={(message) => setError(message || "That action couldn't be completed. Please try again.")}
              />

              <div className="export-row">
                <a className="btn" href={exportUrl(selectedScan.scan_id, "csv")} download>
                  <ArrowDownToLine size={13} /> Export CSV
                </a>
                <a className="btn" href={exportUrl(selectedScan.scan_id, "json")} download>
                  <ArrowDownToLine size={13} /> Export JSON
                </a>
              </div>

              <PagesTable scanId={selectedScan.scan_id} pages={pages} loading={loading} />
            </>
          ) : (
            <p className="empty-block">
              {loading
                ? "Loading…"
                : scanning
                  ? "Results will appear here when the scan finishes."
                  : scans.length
                    ? "Select a completed scan from History to view its report, or run a new scan."
                    : "No scans yet. Enter a website URL above and choose Run Scan."}
            </p>
          )}
        </div>
      </main>
    </div>
  );
}
