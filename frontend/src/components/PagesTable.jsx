
import { useEffect, useState } from "react";

const PAGE_SIZE = 10;

export default function PagesTable({ scanId, pages, loading }) {
  const [page, setPage] = useState(1);

  useEffect(() => {
    setPage(1);
  }, [scanId]);

  const pageCount = Math.max(1, Math.ceil(pages.length / PAGE_SIZE));
  const current = Math.min(page, pageCount);
  const visible = pages.slice(
    (current - 1) * PAGE_SIZE,
    current * PAGE_SIZE
  );

  return (
    <section className="panel pages" aria-label="Crawled pages">
      <div className="panel-head">
        <h2>Pages crawled</h2>
        <span className="muted small">{pages.length} total</span>
      </div>

      <div className="table-wrap">
        <table className="data-table pages-table">
          <thead>
            <tr>
              <th>Page URL</th>
              <th>HTTP</th>
              <th>Depth</th>
              <th>Priority</th>
              <th>Redirects</th>
              <th>Title</th>
              <th>Priority reason</th>
            </tr>
          </thead>

          <tbody>
            {visible.map((item) => (
              <tr key={item.page_id}>
                <td
                  data-label="URL"
                  className="url"
                  title={item.url}
                >
                  {item.url}
                </td>

                <td data-label="HTTP">
                  <span
                    className={`http ${
                      item.http_status >= 400 ? "bad" : ""
                    }`}
                  >
                    {item.http_status ?? "—"}
                  </span>
                </td>

                <td data-label="Depth">
                  {item.depth ?? "—"}
                </td>

                <td data-label="Priority">
                  {item.priority ?? "—"}
                </td>

                <td data-label="Redirects">
                  {item.redirect_count ?? 0}
                </td>

                <td data-label="Title">
                  {item.title || (
                    <span className="muted">
                      {item.error ? "Request failed" : "No title"}
                    </span>
                  )}
                </td>

                <td
                  data-label="Priority reason"
                  className="priority-reason"
                  title={item.priority_reason || ""}
                >
                  {item.priority_reason || (
                    <span className="muted">
                      No priority evidence available
                    </span>
                  )}
                </td>
              </tr>
            ))}

            {!visible.length && (
              <tr>
                <td colSpan={7} className="empty">
                  {loading
                    ? "Loading pages…"
                    : "No pages were recorded for this scan."}
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      {pageCount > 1 && (
        <div className="table-foot">
          <span className="muted small">
            Page {current} of {pageCount}
          </span>

          <div className="pager">
            <button
              className="btn"
              disabled={current <= 1}
              onClick={() => setPage(current - 1)}
            >
              Previous
            </button>

            <button
              className="btn"
              disabled={current >= pageCount}
              onClick={() => setPage(current + 1)}
            >
              Next
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
