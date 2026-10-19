export const API_BASE_URL =
  import.meta.env?.VITE_API_BASE_URL || "http://127.0.0.1:8000/api";

async function apiRequest(path, options = {}) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      headers: {
        "Content-Type": "application/json",
        ...options.headers,
      },
    });
  } catch {
    throw new Error(
      "Could not reach the SiteAudit API. Check that the backend is running."
    );
  }

  const data = await response.json().catch(() => ({}));

  if (!response.ok) {
    const detail = Array.isArray(data.detail)
      ? data.detail.map((item) => item.msg).join("; ")
      : data.detail;
    throw new Error(detail || `Request failed (${response.status})`);
  }

  return data;
}

export const getHealth = () => apiRequest("/health");

export const getScans = () => apiRequest("/scans");

export const getScan = (scanId) => apiRequest(`/scans/${scanId}`);

export const getScanProgress = (scanId) =>
  apiRequest(`/scans/${scanId}/progress`);

export const getSummary = (scanId) => apiRequest(`/scans/${scanId}/summary`);

export const getIssues = (scanId) => apiRequest(`/scans/${scanId}/issues`);

export const getIssue = (issueId) => apiRequest(`/issues/${issueId}`);

export const getPages = (scanId) => apiRequest(`/scans/${scanId}/pages`);

export const getRecommendations = (scanId) =>
  apiRequest(`/scans/${scanId}/recommendations`);

// crawlDepth: number of link hops from the start page; null = unlimited.
export const startScan = (startUrl, pageLimit = 200, crawlDepth = null) =>
  apiRequest("/scans", {
    method: "POST",
    body: JSON.stringify({
      start_url: startUrl,
      page_limit: Number(pageLimit),
      crawl_depth: crawlDepth === null || crawlDepth === "" ? null : Number(crawlDepth),
    }),
  });

export const recheckIssue = (issueId) =>
  apiRequest(`/issues/${issueId}/recheck`, { method: "POST" });

export const getRechecks = (issueId) => apiRequest(`/issues/${issueId}/rechecks`);

export const exportUrl = (scanId, format) =>
  `${API_BASE_URL}/scans/${scanId}/export?format=${format}`;
