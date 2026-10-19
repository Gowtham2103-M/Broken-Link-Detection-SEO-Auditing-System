import { useEffect, useState } from "react";
import { LoaderCircle, Play } from "lucide-react";

const DEPTH_OPTIONS = [
  { value: "0", label: "Start page only" },
  { value: "1", label: "1 level" },
  { value: "2", label: "2 levels" },
  { value: "3", label: "3 levels" },
  { value: "5", label: "5 levels" },
  { value: "", label: "Unlimited" },
];

function ScanProgress({ progress }) {
  if (!progress) {
    return (
      <div className="progress" role="status">
        <div className="progress-line">
          <LoaderCircle size={14} className="spin" />
          <strong>Starting scan…</strong>
        </div>
      </div>
    );
  }

  const { phase, current, total, percent, total_is_estimate: estimate } = progress;
  const hasTotal = total > 0;

  let title = "Starting scan…";
  let step = "";
  let detail = "";

  if (phase === "crawling") {
    title = "Crawling pages";
    step = "Step 1 of 2";
    detail = hasTotal
      ? `${current} of ${total}${estimate ? "+" : ""} pages`
      : `${current} pages`;
  } else if (phase === "checking_links") {
    title = "Checking links";
    step = "Step 2 of 2";
    detail = hasTotal ? `${current} of ${total} links` : `${current} links`;
  } else if (phase === "saving") {
    title = "Saving results";
    step = "Finishing";
  }

  const showBar = (phase === "crawling" || phase === "checking_links") && hasTotal;

  return (
    <div className="progress" role="status" aria-live="polite">
      <div className="progress-line">
        <LoaderCircle size={14} className="spin" />
        <strong>{title}</strong>
        {step && <span className="muted">{step}</span>}
        <span className="progress-detail">{detail}</span>
      </div>
      <div
        className="progress-track"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={showBar ? percent : 0}
        aria-label={title}
      >
        <span style={{ width: `${showBar ? percent : 0}%` }} />
      </div>
      <p className="progress-note">
        {progress.pages_crawled} pages crawled · {progress.links_checked} links checked
        {estimate ? " · total grows as new pages are discovered" : ""}
      </p>
    </div>
  );
}

export default function ScanForm({ onSubmit, scanning, progress, defaults }) {
  const [url, setUrl] = useState(defaults?.url || "");
  const [depth, setDepth] = useState(
    defaults?.crawlDepth == null ? "" : String(defaults.crawlDepth)
  );
  const [pageLimit, setPageLimit] = useState(
    String(defaults?.pageLimit || 50)
  );
  const [urlError, setUrlError] = useState("");

  useEffect(() => {
    if (!defaults) return;

    setUrl(defaults.url || "");
    setDepth(defaults.crawlDepth == null ? "" : String(defaults.crawlDepth));
    setPageLimit(String(defaults.pageLimit || 50));
  }, [defaults]);

  const limitNumber = Number(pageLimit);
  const limitValid = Number.isInteger(limitNumber) && limitNumber >= 1 && limitNumber <= 200;

  function submit(event) {
  event.preventDefault();

  if (!limitValid) return;

  const trimmedUrl = url.trim();
  let parsedUrl;

  try {
    parsedUrl = new URL(trimmedUrl);
  } catch {
    setUrlError("Enter a valid website URL, such as https://example.com.");
    return;
  }

  if (
    !["http:", "https:"].includes(parsedUrl.protocol) ||
    !parsedUrl.hostname.includes(".") ||
    parsedUrl.hostname.startsWith(".") ||
    parsedUrl.hostname.endsWith(".") ||
    /\s/.test(parsedUrl.hostname)
  ) {
    setUrlError("Enter a valid website URL starting with http:// or https://.");
    return;
  }

  setUrlError("");

  onSubmit({
    url: trimmedUrl,
    pageLimit: limitNumber,
    crawlDepth: depth === "" ? null : Number(depth),
  });
}

  return (
    <section className="panel scan-panel" aria-label="Run a scan">
      <form onSubmit={submit} className="scan-form">
        <div className="field grow">
          <label htmlFor="website-url">Website URL</label>
          <input
  id="website-url"
  type="text"
  inputMode="url"
  autoComplete="url"
  value={url}
  onChange={(event) => {
    setUrl(event.target.value);
    setUrlError("");
  }}
  placeholder="https://example.com"
  aria-invalid={Boolean(urlError)}
  aria-describedby={urlError ? "website-url-error" : undefined}
  required
  disabled={scanning}
/>

{urlError && (
  <p id="website-url-error" className="field-error" role="alert">
    {urlError}
  </p>
)}
        </div>

        <div className="field">
          <label htmlFor="crawl-depth">Crawl depth</label>
          <select
            id="crawl-depth"
            value={depth}
            onChange={(event) => setDepth(event.target.value)}
            disabled={scanning}
          >
            {DEPTH_OPTIONS.map((option) => (
              <option key={option.label} value={option.value}>{option.label}</option>
            ))}
          </select>
        </div>

        <div className="field narrow">
          <label htmlFor="max-pages">Max pages</label>
          <input
            id="max-pages"
            type="number"
            min="1"
            max="200"
            step="1"
            value={pageLimit}
            onChange={(event) => setPageLimit(event.target.value)}
            aria-invalid={!limitValid}
            disabled={scanning}
          />
        </div>

        <button className="btn primary run" type="submit" disabled={scanning || !limitValid || !url.trim()}>
          {scanning ? <LoaderCircle size={15} className="spin" /> : <Play size={14} />}
          {scanning ? "Scanning…" : "Run Scan"}
        </button>
      </form>

      {!limitValid && <p className="field-error">Maximum pages must be a whole number from 1 to 200.</p>}
      {scanning && <ScanProgress progress={progress} />}
    </section>
  );
}
